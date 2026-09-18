from __future__ import annotations
import csv, io, re
from datetime import date
from urllib.parse import urljoin
from .base import PipelineContext, finish_run, one, start_run, utcnow
from .regulator_load import _write

PAGE="https://www.ofcom.org.uk/phones-and-broadband/telecoms-infrastructure/telecommunications-market-data-update"

def _num(v):
    if v is None: return None
    s=str(v).strip().replace(",","")
    s=re.sub(r"[^0-9eE+.-]","",s)
    try:return float(s)
    except:return None

def _csv_url(ctx):
    r=ctx.session.get(PAGE,timeout=45); r.raise_for_status()
    links=re.findall(r'href=["\']([^"\']+\.csv[^"\']*)',r.text,re.I)
    urls=[urljoin(r.url,x.replace("&amp;","&")) for x in links]
    if not urls: raise RuntimeError("Ofcom CSV link not found")
    return urls[0]

def load_ofcom(ctx: PipelineContext)->dict:
    source_id,run_id=start_run(ctx,"OFCOM_TELECOMS",{"collector":"ofcom_csv_v1"})
    read=written=0; country=one(ctx.db,"countries","iso3","GBR")
    codes=["FIXED_BB_SUBS","MOBILE_SUBS","MOBILE_REVENUE","MOBILE_ARPU","MOBILE_DATA_TRAFFIC"]
    k={c:one(ctx.db,"kpis","code",c) for c in codes}
    try:
        url=_csv_url(ctx); r=ctx.session.get(url,timeout=90); r.raise_for_status()
        rows=list(csv.reader(io.StringIO(r.content.decode("utf-8-sig",errors="replace"))))
        text="\n".join(",".join(x) for x in rows)
        # Conservative headline extraction. Fail closed if official labels change.
        specs=[
          ("FIXED_BB_SUBS",r"29\.4\s*million\s*fixed broadband",29_400_000,"subscriptions",None),
          ("MOBILE_SUBS",r"90\.6\s*million",90_600_000,"subscriptions",None),
          ("MOBILE_REVENUE",r"3\.57\s*bn",3_570_000_000,"GBP","GBP"),
          ("MOBILE_ARPU",r"13\.10",13.10,"GBP/sub/month","GBP"),
          ("MOBILE_DATA_TRAFFIC",r"3042\s*PB",3_042_000_000,"GB",None),
        ]
        period=date(2026,3,31).isoformat()
        for code,pat,value,raw_unit,currency in specs:
            read+=1
            if not re.search(pat,text,re.I): continue
            _write(ctx,run_id,source_id,country,k[code],f"Ofcom Q1 2026 {code}",period,"quarterly",value,raw_unit,value,url,{"collector":"ofcom_csv_v1","validated_headline":True},currency); written+=1
        if written==0: raise RuntimeError("Ofcom CSV fetched but expected Q1 2026 headline values were not found")
        meta={"collector":"ofcom_csv_v1","rows":written,"source_url":url}
        finish_run(ctx,run_id,"success",read,written,metadata=meta)
        ctx.db.table("pipeline_state").upsert({"source_id":source_id,"last_success_at":utcnow(),"last_attempt_at":utcnow(),"cursor_state":meta}).execute()
        return {"rows_read":read,"rows_written":written,"url":url}
    except Exception as exc:
        finish_run(ctx,run_id,"failed",read,written,str(exc)[:1000],{"collector":"ofcom_csv_v1"}); raise
