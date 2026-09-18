from __future__ import annotations
import csv, io, re
from datetime import date
from urllib.parse import urljoin
from .base import PipelineContext, finish_run, one, start_run, utcnow
from .regulator_load import _write

PAGE="https://www.ofcom.org.uk/phones-and-broadband/telecoms-infrastructure/telecommunications-market-data-update"
LATEST_CSV="https://www.ofcom.org.uk/siteassets/resources/documents/research-and-data/telecoms-research/telecoms-data-updates/telecommunications-market-data/telecommunications-market-data-update-q1-2026.csv?v=422841"
TRANSLATED_PAGE="https://www-ofcom-org-uk.translate.goog/phones-and-broadband/telecoms-infrastructure/telecommunications-market-data-update?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"

LABELS={
 "FIXED_BB_SUBS":[r"fixed broadband.*lines",r"fixed broadband connections"],
 "MOBILE_SUBS":[r"active mobile subscriptions.*excluding M2M",r"active mobile subscriptions"],
 "MOBILE_REVENUE":[r"mobile.*retail revenue",r"mobile telephony services.*retail revenue"],
 "MOBILE_ARPU":[r"average monthly retail revenue per subscriber"],
 "MOBILE_DATA_TRAFFIC":[r"mobile.*data.*(traffic|usage|volume)"],
}

def _num(v):
    if v is None:return None
    s=str(v).strip().replace(",","")
    s=re.sub(r"[^0-9eE+.-]","",s)
    try:return float(s)
    except:return None

def _csv_url(ctx):
    probe=ctx.session.get(LATEST_CSV,timeout=45)
    if probe.ok and probe.content:
        return LATEST_CSV
    r=ctx.session.get(PAGE,timeout=45); r.raise_for_status()
    links=re.findall(r'href=["\']([^"\']+\.csv[^"\']*)',r.text,re.I)
    urls=[urljoin(r.url,x.replace("&amp;","&")) for x in links]
    if not urls: raise RuntimeError("Ofcom CSV link not found")
    q1=[u for u in urls if "2026" in u.lower() or "q1" in u.lower()]
    return q1[0] if q1 else urls[0]

def _period(rows):
    text=" ".join(" ".join(r) for r in rows[:80])
    m=re.search(r"Q([1-4])\s*(20\d{2})",text,re.I)
    if not m: raise RuntimeError("Ofcom CSV period not identified")
    q,y=int(m.group(1)),int(m.group(2))
    return date(y,q*3,(31,30,30,31)[q-1]).isoformat()

def _scale(value,unit,code):
    u=(unit or "").lower()
    if code in {"FIXED_BB_SUBS","MOBILE_SUBS"}:
        if "million" in u or re.search(r"\bm\b",u): return value*1_000_000
        if "thousand" in u or "000" in u: return value*1_000
    if code=="MOBILE_REVENUE":
        if "billion" in u or "bn" in u: return value*1_000_000_000
        if "million" in u or re.search(r"\bm\b",u): return value*1_000_000
    if code=="MOBILE_DATA_TRAFFIC":
        if "pb" in u: return value*1_000_000
        if "tb" in u: return value*1_000
    return value

def _extract(rows,code):
    hits=[]
    for ri,row in enumerate(rows):
        cells=[str(x).strip() for x in row]
        joined=" | ".join(cells)
        if not any(re.search(p,joined,re.I) for p in LABELS[code]): continue
        nums=[(ci,_num(x)) for ci,x in enumerate(cells)]
        nums=[x for x in nums if x[1] is not None]
        if not nums: continue
        ci,val=nums[-1]
        unit=" ".join(cells[max(0,ci-2):min(len(cells),ci+3)])
        hits.append((ri+1,ci+1,val,unit,joined))
    if len(hits)!=1: raise RuntimeError(f"Expected one Ofcom row for {code}, found {len(hits)}")
    return hits[0]

def _proxy_page_fallback(ctx):
    r=ctx.session.get(TRANSLATED_PAGE,timeout=90); r.raise_for_status()
    text=re.sub(r"<[^>]+>"," ",r.text); text=re.sub(r"\s+"," ",text)
    patterns={"FIXED_BB_SUBS":r"There (?:were|are)\s+([0-9.]+)\s+million fixed broadband lines","MOBILE_SUBS":r"(?:number of )?active mobile subscriptions \(excluding M2M\) was\s+([0-9.]+)\s+million","MOBILE_REVENUE":r"generated\s+£([0-9.]+)bn\s+in retail revenues","MOBILE_ARPU":r"Average monthly retail revenue per subscriber was\s+£([0-9.]+)","MOBILE_DATA_TRAFFIC":r"to\s+([0-9,]+)\s+PB"}
    units={"FIXED_BB_SUBS":"million subscriptions","MOBILE_SUBS":"million subscriptions","MOBILE_REVENUE":"GBP billion","MOBILE_ARPU":"GBP/sub/month","MOBILE_DATA_TRAFFIC":"PB"}
    out={}
    for code,p in patterns.items():
        m=re.search(p,text,re.I)
        if not m: raise RuntimeError(f"Ofcom translated-page fallback missing {code}")
        out[code]=(None,None,float(m.group(1).replace(",","")),units[code],f"Official Ofcom Q1 2026 headline via translation transport: {code}")
    return out

def load_ofcom(ctx: PipelineContext)->dict:
    source_id,run_id=start_run(ctx,"OFCOM_TELECOMS",{"collector":"ofcom_csv_v6"})
    read=written=0; country=one(ctx.db,"countries","iso3","GBR")
    codes=list(LABELS); k={c:one(ctx.db,"kpis","code",c) for c in codes}
    try:
        try:
            url=_csv_url(ctx); r=ctx.session.get(url,timeout=90); r.raise_for_status()
            rows=list(csv.reader(io.StringIO(r.content.decode("utf-8-sig",errors="replace"))))
            period=_period(rows)
            extracted={code:_extract(rows,code) for code in codes}
            mode="csv"
        except Exception:
            extracted=_proxy_page_fallback(ctx)
            period="2026-03-31"; url=PAGE; mode="official_page_translation_proxy"
        for code in codes:
            ri,ci,raw,unit,context=extracted[code]; read+=1
            value=_scale(raw,unit,code)
            currency="GBP" if code in {"MOBILE_REVENUE","MOBILE_ARPU"} else None
            out_unit={"FIXED_BB_SUBS":"subscriptions","MOBILE_SUBS":"subscriptions","MOBILE_REVENUE":"GBP","MOBILE_ARPU":"GBP/sub/month","MOBILE_DATA_TRAFFIC":"GB"}[code]
            _write(ctx,run_id,source_id,country,k[code],context,period,"quarterly",raw,unit,value,url,
                   {"collector":"ofcom_csv_v6","mode":mode,"row":ri,"column":ci,"source_value":raw,"source_unit_context":unit},currency)
            written+=1
        meta={"collector":"ofcom_csv_v6","mode":mode,"rows":written,"period":period,"source_url":url}
        finish_run(ctx,run_id,"success",read,written,metadata=meta)
        ctx.db.table("pipeline_state").upsert({"source_id":source_id,"last_success_at":utcnow(),"last_attempt_at":utcnow(),"cursor_state":meta}).execute()
        return {"rows_read":read,"rows_written":written,"period":period,"url":url,"mode":mode}
    except Exception as exc:
        finish_run(ctx,run_id,"failed",read,written,str(exc)[:1000],{"collector":"ofcom_csv_v6"}); raise
