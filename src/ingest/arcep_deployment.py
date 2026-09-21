from __future__ import annotations

import io
import re
from datetime import date

from openpyxl import load_workbook

from .base import PipelineContext, finish_run, one, start_run, utcnow
from .arcep_load import _safe_float, _upsert_obs, _upsert_raw

DATASET_API = "https://www.data.gouv.fr/api/1/datasets/le-marche-du-haut-et-tres-haut-debit-fixe-deploiements/"


def _quarter(title: str):
    m = re.search(r"(20\d{2})\s*[TQ]([1-4])", title or "", re.I)
    if not m:
        return None
    y, q = int(m.group(1)), int(m.group(2))
    return date(y, q * 3, (31, 30, 30, 31)[q - 1]).isoformat()


def _latest_workbook(dataset: dict):
    candidates = []
    for r in dataset.get("resources", []):
        title = r.get("title") or ""
        fmt = (r.get("format") or "").lower()
        if fmt != "xlsx" or "obs-hd-thd-deploiement" not in title.lower():
            continue
        period = _quarter(title)
        if period and (r.get("latest") or r.get("url")):
            candidates.append((period, r))
    if not candidates:
        raise RuntimeError("ARCEP deployment workbook not found")
    return max(candidates, key=lambda x: x[0])


def _national_ftth_total(wb):
    # Couverture has a stable structural layout: quarterly headers on row 4,
    # national premises on row 7, and national FttH eligibility on row 21.
    # Read those explicit national series directly; never aggregate zones/operators.
    ws = next((s for s in wb.worksheets if s.title.lower() == "couverture"), None)
    if ws is None:
        raise RuntimeError("ARCEP Couverture sheet not found")

    periods = [cell.value for cell in ws[4]]
    premises = [cell.value for cell in ws[7]]
    ftth_rate = [cell.value for cell in ws[21]]

    if str(premises[0]).strip().lower() != "france entière":
        raise RuntimeError(f"Unexpected Couverture row 7 label: {premises[0]!r}")
    if str(ftth_rate[0]).strip().lower() != "france entière":
        raise RuntimeError(f"Unexpected Couverture row 21 label: {ftth_rate[0]!r}")

    candidates = []
    for ci, raw_label in enumerate(periods):
        label = "" if raw_label is None else str(raw_label).strip()
        if not re.fullmatch(r"20\\d{2} [TQ][1-4]", label, re.I):
            continue
        if ci >= len(premises) or ci >= len(ftth_rate):
            continue
        p = _safe_float(premises[ci])
        rate = _safe_float(ftth_rate[ci])
        if p is not None and rate is not None and p > 1_000_000 and 0 <= rate <= 1:
            candidates.append((label, ci, p, rate))

    if not candidates:
        raise RuntimeError("No aligned ARCEP premises/FttH coverage quarter found in Couverture rows 4/7/21")

    label, ci, p, rate = max(candidates, key=lambda x: _quarter(x[0]) or "")
    value = round(p * rate)
    context = f"{label}: France entière locaux={p}; taux éligibles FttH={rate}; derived raccordables={value}"
    return ws.title, 21, ci + 1, value, context


def load_arcep_deployment(ctx: PipelineContext) -> dict:
    source_id, run_id = start_run(ctx, "ARCEP_OBS", {"collector": "arcep_deployment_v5"})
    read = written = 0
    try:
        country = one(ctx.db, "countries", "iso3", "FRA")
        kpi = one(ctx.db, "kpis", "code", "FTTH_HOMES_PASSED")
        r = ctx.session.get(DATASET_API, timeout=45); r.raise_for_status()
        dataset = r.json()
        period, res = _latest_workbook(dataset)
        url = res.get("latest") or res.get("url")
        x = ctx.session.get(url, timeout=120); x.raise_for_status()
        wb = load_workbook(io.BytesIO(x.content), read_only=True, data_only=True)
        sheet, row, col, value, label = _national_ftth_total(wb)
        read = 1
        raw = {
            "ingestion_run_id": run_id, "source_id": source_id, "country_id": country["id"], "operator_id": None,
            "source_indicator": "Locaux raccordables FTTH - France", "period_date": period, "frequency": "quarterly",
            "value_numeric": value, "unit_raw": "locaux raccordables", "source_url": url, "retrieved_at": utcnow(),
            "payload": {"dataset_id": dataset.get("id"), "resource_id": res.get("id"), "resource_title": res.get("title"),
                        "sheet": sheet, "row": row, "column": col, "matched_context": label},
            "source_record_key": f"{res.get('id')}:{sheet}:{row}:{col}"
        }
        raw_id = _upsert_raw(ctx, raw)
        obs = {"kpi_id": kpi["id"], "country_id": country["id"], "operator_id": None, "period_date": period,
               "frequency": "quarterly", "value": value, "unit": "premises", "currency_code": None,
               "source_id": source_id, "raw_observation_id": raw_id, "definition_version": kpi["definition_version"],
               "quality_flag": "ok", "retrieved_at": utcnow(),
               "quality_notes": "ARCEP locaux raccordables FTTH; explicit national total, not summed across zones/operators."}
        _upsert_obs(ctx, obs); written = 1
        meta = {"collector": "arcep_deployment_v5", "period": period, "resource_id": res.get("id"), "source_url": url}
        finish_run(ctx, run_id, "success", read, written, metadata=meta)
        ctx.db.table("pipeline_state").upsert({"source_id": source_id, "last_success_at": utcnow(),
                                               "last_attempt_at": utcnow(), "cursor_state": meta}).execute()
        return {"rows_read": read, "rows_written": written, **meta}
    except Exception as exc:
        finish_run(ctx, run_id, "failed", read, written, str(exc)[:1000], {"collector": "arcep_deployment_v5"})
        raise
