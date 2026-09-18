from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from urllib.parse import urljoin

from .base import PipelineContext, finish_run, one, start_run, utcnow
from .regulator_load import _write

PAGE = "https://www.ofcom.org.uk/phones-and-broadband/coverage-and-speeds/connected-nations-update-spring-2026"


def _zip_url(ctx: PipelineContext) -> str:
    r = ctx.session.get(PAGE, timeout=45); r.raise_for_status()
    links = re.findall(r'href=["\']([^"\']+\.zip[^"\']*)', r.text, re.I)
    urls = [urljoin(r.url, x.replace("&amp;", "&")) for x in links]
    fixed = [u for u in urls if "fixed" in u.lower() or "fibre" in u.lower()]
    if len(fixed) != 1:
        raise RuntimeError(f"Expected one Ofcom fixed/full-fibre ZIP, found {len(fixed)}")
    return fixed[0]


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def _find_col(headers, patterns):
    hits = [i for i, h in enumerate(headers) if any(re.search(p, _norm(h), re.I) for p in patterns)]
    if len(hits) != 1:
        raise RuntimeError(f"Expected one matching column for {patterns}, found {len(hits)}")
    return hits[0]


def _num(v):
    s = str(v or "").strip().replace(",", "").replace("%", "")
    try: return float(s)
    except: return None


def _uk_coverage(z: zipfile.ZipFile):
    names = [n for n in z.namelist() if re.search(r"fixed_coverage_UK_and_nations.*\.csv$", n, re.I)]
    if len(names) != 1:
        raise RuntimeError(f"Expected one UK/nations coverage CSV, found {len(names)}")
    rows = list(csv.reader(io.TextIOWrapper(z.open(names[0]), encoding="utf-8-sig")))
    h = rows[0]
    loc = _find_col(h, [r"^location$"])
    premise = _find_col(h, [r"premise type"])
    ff_count = _find_col(h, [r"full.?fibre.*(premises|count|number)", r"(premises|count|number).*full.?fibre"])
    matches = [r for r in rows[1:] if _norm(r[loc]) == "uk" and _norm(r[premise]) == "residential"]
    vals = [_num(r[ff_count]) for r in matches if _num(r[ff_count]) is not None]
    vals = [v for v in vals if v > 1_000_000]
    if len(vals) != 1:
        raise RuntimeError(f"Expected one UK residential full-fibre premises value, found {len(vals)}")
    return vals[0], names[0], h[ff_count]


def _summary_takeup(z: zipfile.ZipFile):
    # National take-up is published in Ofcom summary tables, not inferred by
    # dividing residential coverage by all-premises active lines.
    names = [n for n in z.namelist() if re.search(r"(summary|table).*\.csv$", n, re.I)]
    hits = []
    for name in names:
        rows = list(csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig")))
        for ri, row in enumerate(rows):
            text = " | ".join(row)
            if re.search(r"UK", text, re.I) and re.search(r"full.?fibre.*take.?up|take.?up.*full.?fibre", text, re.I):
                nums = [_num(x) for x in row]
                nums = [x for x in nums if x is not None and 0 <= x <= 100]
                if len(nums) == 1: hits.append((nums[0], name, ri + 1, text))
    if len(hits) != 1:
        raise RuntimeError(f"Expected one explicit UK full-fibre take-up percentage, found {len(hits)}")
    return hits[0]


def load_ofcom_connected_nations(ctx: PipelineContext) -> dict:
    source_id, run_id = start_run(ctx, "OFCOM_TELECOMS", {"collector": "ofcom_connected_nations_v1"})
    read = written = 0
    try:
        country = one(ctx.db, "countries", "iso3", "GBR")
        kp = {c: one(ctx.db, "kpis", "code", c) for c in ("FTTH_HOMES_PASSED", "FTTH_TAKEUP")}
        url = _zip_url(ctx); r = ctx.session.get(url, timeout=180); r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        period = date(2026, 1, 31).isoformat()

        premises, fname, label = _uk_coverage(z); read += 1
        _write(ctx, run_id, source_id, country, kp["FTTH_HOMES_PASSED"], label, period, "snapshot",
               premises, "residential premises", premises, url,
               {"collector":"ofcom_connected_nations_v1","file":fname,
                "definition":"UK residential premises with full-fibre availability"}, None)
        written += 1

        # Do not derive take-up from coverage: Ofcom's national take-up denominator
        # covers all premises, while headline coverage is residential.
        takeup, tfile, trow, context = _summary_takeup(z); read += 1
        _write(ctx, run_id, source_id, country, kp["FTTH_TAKEUP"], context, period, "snapshot",
               takeup, "percent", takeup, url,
               {"collector":"ofcom_connected_nations_v1","file":tfile,"row":trow,
                "definition":"Ofcom published UK full-fibre take-up; all premises"}, None)
        written += 1

        meta={"collector":"ofcom_connected_nations_v1","period":period,"source_url":url,"rows":written}
        finish_run(ctx, run_id, "success", read, written, metadata=meta)
        ctx.db.table("pipeline_state").upsert({"source_id":source_id,"last_success_at":utcnow(),
            "last_attempt_at":utcnow(),"cursor_state":meta}).execute()
        return {"rows_read":read,"rows_written":written,**meta}
    except Exception as exc:
        finish_run(ctx, run_id, "failed", read, written, str(exc)[:1000],
                   {"collector":"ofcom_connected_nations_v1"})
        raise
