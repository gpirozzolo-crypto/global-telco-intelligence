from __future__ import annotations

import io
import re
from urllib.parse import urljoin

from openpyxl import load_workbook

from .base import PipelineContext, finish_run, start_run

AGCOM_PAGE = "https://www.agcom.it/pubblicazioni/osservatori/osservatorio-sulle-comunicazioni-n-2-2026"
BNETZA_PAGE = "https://www.bundesnetzagentur.de/DE/Fachthemen/Telekommunikation/Marktdaten/artikel.html"
CNMC_PAGES = [
    "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/conjuntos-de-datos/datos-mensuales/telecomunicaciones",
    "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/datos-trimestrales/datos-generales/telecomunicaciones",
    "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/datos-trimestrales/datos-de-mercados/telecomunicaciones-3",
]
CNMC_DATASTORE = "https://catalogodatos.cnmc.es/api/3/action/datastore_search"


def _workbook_preview(ctx: PipelineContext, url: str) -> dict:
    r = ctx.session.get(url, timeout=90, allow_redirects=True); r.raise_for_status()
    wb = load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        preview = []
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 15), values_only=True):
            preview.append([None if v is None else str(v)[:180] for v in row[:15]])
        sheets.append({"title": ws.title, "rows": ws.max_row, "cols": ws.max_column, "preview": preview})
    return {"url": r.url, "bytes": len(r.content), "sheets": sheets}


def inspect_agcom(ctx: PipelineContext) -> dict:
    _, run_id = start_run(ctx, "AGCOM_OBS", {"collector":"agcom_xlsx_inspect_v1"})
    try:
        r = ctx.session.get(AGCOM_PAGE, timeout=30); r.raise_for_status()
        links = re.findall(r'href=["\']([^"\']+\.xlsx[^"\']*)', r.text, re.I)
        links = list(dict.fromkeys(urljoin(r.url, x.replace("&amp;", "&")) for x in links))
        books = [_workbook_preview(ctx, u) for u in links[:3]]
        finish_run(ctx, run_id, "success", len(links), 0, metadata={"collector":"agcom_xlsx_inspect_v1","page":r.url,"workbooks":books})
        return {"workbooks":len(books),"links":len(links)}
    except Exception as exc:
        finish_run(ctx, run_id, "failed", 0, 0, str(exc)[:1000], {"collector":"agcom_xlsx_inspect_v1"}); raise


def inspect_bnetza(ctx: PipelineContext) -> dict:
    _, run_id = start_run(ctx, "BNetzA_TK", {"collector":"bnetza_xlsx_inspect_v1"})
    try:
        r = ctx.session.get(BNETZA_PAGE, timeout=30); r.raise_for_status()
        links = re.findall(r'href=["\']([^"\']+\.(?:xlsx|xlsm)[^"\']*)', r.text, re.I)
        links = list(dict.fromkeys(urljoin(r.url, x.replace("&amp;", "&")) for x in links))
        current = [u for u in links if "25_Daten_TK" in u] or links[:1]
        books = [_workbook_preview(ctx, u) for u in current[:2]]
        finish_run(ctx, run_id, "success", len(links), 0, metadata={"collector":"bnetza_xlsx_inspect_v1","page":r.url,"workbooks":books})
        return {"workbooks":len(books),"links":len(links)}
    except Exception as exc:
        finish_run(ctx, run_id, "failed", 0, 0, str(exc)[:1000], {"collector":"bnetza_xlsx_inspect_v1"}); raise


def _cnmc_profile(records: list[dict]) -> dict:
    periods = sorted({str(r.get("trimestre") or r.get("mes") or "") for r in records if r.get("trimestre") or r.get("mes")}, reverse=True)
    latest = periods[0] if periods else None
    latest_rows = [r for r in records if str(r.get("trimestre") or r.get("mes") or "") == latest]
    pairs = sorted({(str(r.get("servicio") or ""), str(r.get("concepto") or "")) for r in latest_rows})
    keep = {"trimestre","mes","servicio","concepto","operador","tecnologia_de_acceso","tipo_de_mercado","tipo_de_ingreso","unidades","ingresos","ingresos_por_operador","lineas","lineas_o_accesos","lineas_o_accesos_por_operador","tasa_de_penetracion","trafico_de_datos"}
    samples = [{k:v for k,v in r.items() if k in keep and v not in (None,"N/A")} for r in latest_rows[:30]]
    return {"latest_period":latest,"service_concepts":[{"servicio":a,"concepto":b} for a,b in pairs[:100]],"latest_sample":samples}


def inspect_cnmc(ctx: PipelineContext) -> dict:
    _, run_id = start_run(ctx, "CNMC_TELCO", {"collector":"cnmc_dataset_inspect_v3"})
    pages = []
    try:
        for url in CNMC_PAGES:
            r = ctx.session.get(url, timeout=30); r.raise_for_status()
            ids = list(dict.fromkeys(re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', r.text, re.I)))
            resource_ids = [x for x in ids if x != "40f418dd-43a0-481a-9017-90ef982b4448"]
            resources=[]
            for rid in resource_ids[:3]:
                api=ctx.session.get(CNMC_DATASTORE,params={"resource_id":rid,"limit":5000},headers={"User-Agent":"GlobalTelcoIntelligence/1.0"},timeout=90)
                item={"resource_id":rid,"status":api.status_code}
                if api.ok:
                    data=api.json().get("result",{}); records=data.get("records",[])
                    item.update({"total":data.get("total"),"fields":[f.get("id") for f in data.get("fields",[])],"profile":_cnmc_profile(records)})
                else: item["error"]=api.text[:300]
                resources.append(item)
            pages.append({"url":r.url,"bytes":len(r.content),"resource_ids":resource_ids[:10],"resources":resources})
        finish_run(ctx, run_id, "success", len(pages), 0, metadata={"collector":"cnmc_dataset_inspect_v3","pages":pages})
        return {"pages":len(pages),"resource_ids":sum(len(x["resource_ids"]) for x in pages),"api_resources":sum(len(x["resources"]) for x in pages)}
    except Exception as exc:
        finish_run(ctx, run_id, "failed", len(pages), 0, str(exc)[:1000], {"collector":"cnmc_dataset_inspect_v3","pages":pages}); raise
