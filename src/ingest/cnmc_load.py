from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .base import PipelineContext, finish_run, one, start_run, utcnow

DATASTORE = "https://catalogodatos.cnmc.es/api/3/action/datastore_search"
MARKETS_RESOURCE = "8ea25e53-b955-4a42-bca4-0a7183237844"
GENERAL_RESOURCE = "73e962dc-ab8f-4994-81c2-352146e1f7c0"
MARKETS_URL = "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/datos-trimestrales/datos-de-mercados/telecomunicaciones-3"
GENERAL_URL = "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/datos-trimestrales/datos-generales/telecomunicaciones"

RULES = [
    ("MOBILE_SUBS", "Telefonía móvil", "Líneas", "lineas_o_accesos", None),
    ("FIXED_BB_SUBS", "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", None),
    ("FTTH_SUBS", "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", {"tecnologia_de_acceso":"FTTH"}),
    ("FTTH_HOMES_PASSED", "Red de distribución", "Accesos", "lineas_o_accesos", {"tecnologia_de_acceso":"FTTH","tipo_de_acceso_de_infraestructuras":"Acceso instalado"}),
    ("MOBILE_REVENUE", "Telefonía móvil", "Ingresos", "ingresos", None),
    ("FIXED_REVENUE", "Banda ancha fija minorista", "Ingresos", "ingresos", None),
    ("MOBILE_DATA_TRAFFIC", "Banda Ancha móvil", "Tráfico - datos", "trafico_de_datos", None),
]

# Every populated field below is a semantic dimension. In particular,
# tipo_de_ingreso must not be ignored: otherwise the unique "Otros" row can be
# mistaken for total service revenue. _country_total may safely sum a single
# mutually-exclusive dimension when all other dimensions are aggregate/N/A.
DIMENSIONS = ["tipo_de_mercado","tipo_de_cliente","segmento","tipo_de_trafico","tipo_de_contrato","tipo_de_linea",
              "tipo_de_mensaje","tipo_de_trafico_de_mensaje","tecnologia_de_acceso","velocidad_baf","tipo_de_oferta",
              "tipo_de_tarifa","tipo_de_ce_minorista","tipo_de_circuito","tipo_de_emision","tipo_de_operador","tipo_de_medio",
              "tipo_de_publicidad","tipo_de_contratacion","tipo_servicio_audiovisual_mayorista","tipo_de_ba_may",
              "tipo_de_interconexion","tipo_de_tarificacion_en_interconexion","tipo_de_ambito","tipo_de_acceso_de_infraestructuras",
              "tipo_de_ingreso","tipo_de_paquete"]

def _na(v): return v in (None, "", "N/A")

def _period(s):
    m=re.fullmatch(r"(20\d{2})T([1-4])", str(s or ""))
    if not m: return None
    y,q=int(m.group(1)),int(m.group(2)); month=q*3; day=(31,30,30,31)[q-1]
    return date(y,month,day).isoformat()

def _records(ctx, resource):
    out=[]; offset=0
    while True:
        r=ctx.session.get(DATASTORE,params={"resource_id":resource,"limit":1000,"offset":offset},timeout=60); r.raise_for_status()
        result=r.json()["result"]; batch=result.get("records",[]); out.extend(batch)
        if len(out)>=result.get("total",0) or not batch: break
        offset += len(batch)
    return out

def _upsert_raw(ctx,p):
    q=(ctx.db.table("raw_observations").select("id").eq("source_id",p["source_id"]).eq("country_id",p["country_id"])
       .eq("source_indicator",p["source_indicator"]).eq("period_date",p["period_date"]).eq("frequency",p["frequency"])
       .is_("operator_id","null").limit(1).execute().data)
    if q: ctx.db.table("raw_observations").update(p).eq("id",q[0]["id"]).execute(); return q[0]["id"]
    return ctx.db.table("raw_observations").insert(p).execute().data[0]["id"]

def _upsert_obs(ctx,p):
    q=(ctx.db.table("observations").select("id").eq("kpi_id",p["kpi_id"]).eq("country_id",p["country_id"])
       .eq("period_date",p["period_date"]).eq("frequency",p["frequency"]).eq("source_id",p["source_id"])
       .is_("operator_id","null").limit(1).execute().data)
    if q: ctx.db.table("observations").update(p).eq("id",q[0]["id"]).execute()
    else: ctx.db.table("observations").insert(p).execute()

def _num(v):
    try: return float(v)
    except (TypeError,ValueError): return None

def _matches(r, service, concept, filters):
    if r.get("servicio") != service or r.get("concepto") != concept or not _na(r.get("operador")): return False
    return all(r.get(k)==v for k,v in (filters or {}).items())

def _operator_total(rows, field):
    # Retail technology totals may be published only by operator. Prefer the
    # operator-specific metric when present; CNMC often leaves the national
    # field blank on those rows.
    named=[]
    for r in rows:
        if _na(r.get("operador")):
            continue
        v=_num(r.get(field))
        if v is None:
            v=_num(r.get(f"{field}_por_operador"))
        if v is not None:
            named.append((r,v))
    labels=[str(r.get("operador")) for r,_ in named]
    if named and len(labels)==len(set(labels)):
        return sum(v for _,v in named), [r for r,_ in named], "sum:operator"
    return None, [], None

def _country_total(rows, field, filters):
    fixed=set((filters or {}).keys())
    dims=[d for d in DIMENSIONS if d not in fixed]
    direct=[r for r in rows if all(_na(r.get(d)) for d in dims) and _num(r.get(field)) is not None]
    if len(direct)==1:
        return _num(direct[0][field]), [direct[0]], "direct_total"
    relaxed=[r for r in rows if all(_na(r.get(d)) for d in dims if d!="tipo_de_mercado") and _num(r.get(field)) is not None]
    if len(relaxed)==1:
        return _num(relaxed[0][field]), [relaxed[0]], "unique_market_total"
    for dim in dims:
        candidates=[r for r in rows if not _na(r.get(dim)) and all(_na(r.get(d)) for d in dims if d!=dim) and _num(r.get(field)) is not None]
        labels=[str(r.get(dim)) for r in candidates]
        if len(candidates)>=2 and len(labels)==len(set(labels)):
            return sum(_num(r[field]) for r in candidates), candidates, f"sum:{dim}"
    return None, [], None

def _write(ctx, run_id, source_id, country, kpi, resource, source_url, rows, code, indicator, field, value, method):
    rec=rows[0]; unit=rec.get("unidades") or kpi["unit"]
    numeric=value*1_000_000 if "Millones de euros" in str(unit) else value
    period=_period(rec.get("trimestre"))
    payload={"resource_id":resource,"aggregation_method":method,"record_ids":[r.get("_id") for r in rows],"records":rows}
    raw={"ingestion_run_id":run_id,"source_id":source_id,"country_id":country["id"],"operator_id":None,
         "source_indicator":indicator,"period_date":period,"frequency":"quarterly","value_text":str(value),
         "value_numeric":value,"unit_raw":unit,"currency_raw":"EUR" if "euros" in str(unit).lower() else None,
         "source_url":source_url,"retrieved_at":utcnow(),"payload":payload,
         "source_record_key":f"{resource}:{period}:{code}"}
    raw_id=_upsert_raw(ctx,raw)
    obs={"kpi_id":kpi["id"],"country_id":country["id"],"operator_id":None,"period_date":period,"frequency":"quarterly",
         "value":numeric,"unit":kpi["unit"],"currency_code":"EUR" if "REVENUE" in code else None,"source_id":source_id,
         "raw_observation_id":raw_id,"definition_version":kpi["definition_version"],"quality_flag":"ok","retrieved_at":utcnow(),
         "quality_notes":f"CNMC country total ({method}): {indicator}"}
    _upsert_obs(ctx,obs)

def load_cnmc(ctx: PipelineContext) -> dict:
    source_id,run_id=start_run(ctx,"CNMC_TELCO",{"collector":"cnmc_quarterly_load_v7"})
    country=one(ctx.db,"countries","iso3","ESP")
    codes={r[0] for r in RULES}|{"TELCO_REVENUE"}
    kpis={c:one(ctx.db,"kpis","code",c) for c in codes}
    read=written=0; matched={}; skipped={}
    try:
        markets=_records(ctx,MARKETS_RESOURCE); general=_records(ctx,GENERAL_RESOURCE); read=len(markets)+len(general)
        periods=sorted({_period(r.get("trimestre")) for r in markets if _period(r.get("trimestre"))})
        for code,service,concept,field,filters in RULES:
            for period in periods:
                rows=[r for r in markets if _period(r.get("trimestre"))==period and _matches(r,service,concept,filters)]
                value,used,method=_country_total(rows,field,filters)
                if value is None and code=="FTTH_SUBS":
                    operator_rows=[r for r in markets if _period(r.get("trimestre"))==period
                                   and r.get("servicio")==service and r.get("concepto")==concept
                                   and all(r.get(a)==b for a,b in (filters or {}).items())]
                    value,used,method=_operator_total(operator_rows,field)
                if value is None:
                    skipped[code]=skipped.get(code,0)+1; continue
                indicator=f"{service} | {concept}" + (" | "+";".join(f"{a}={b}" for a,b in filters.items()) if filters else "")
                _write(ctx,run_id,source_id,country,kpis[code],MARKETS_RESOURCE,MARKETS_URL,used,code,indicator,field,value,method)
                written+=1; matched[code]=matched.get(code,0)+1
        for period in sorted({_period(r.get("trimestre")) for r in general if _period(r.get("trimestre"))}):
            rows=[r for r in general if _period(r.get("trimestre"))==period and r.get("concepto")=="Ingresos" and _na(r.get("operador"))
                  and not _na(r.get("tipo_de_ingreso")) and _na(r.get("tipo_de_paquete")) and _num(r.get("ingresos")) is not None]
            keys=[(r.get("tipo_de_mercado"),r.get("tipo_de_ingreso")) for r in rows]
            if rows and len(keys)==len(set(keys)):
                value=sum(_num(r["ingresos"]) for r in rows)
                _write(ctx,run_id,source_id,country,kpis["TELCO_REVENUE"],GENERAL_RESOURCE,GENERAL_URL,rows,"TELCO_REVENUE",
                       "Datos generales | Ingresos | total", "ingresos",value,"sum:tipo_de_mercado+tipo_de_ingreso")
                written+=1; matched["TELCO_REVENUE"]=matched.get("TELCO_REVENUE",0)+1
        meta={"collector":"cnmc_quarterly_load_v7","resources":[MARKETS_RESOURCE,GENERAL_RESOURCE],"matched":matched,"skipped":skipped}
        finish_run(ctx,run_id,"success",read,written,metadata=meta)
        ctx.db.table("pipeline_state").upsert({"source_id":source_id,"last_success_at":utcnow(),"last_attempt_at":utcnow(),"cursor_state":meta}).execute()
        return {"rows_read":read,"rows_written":written,"matched":matched,"skipped":skipped}
    except Exception as exc:
        finish_run(ctx,run_id,"failed",read,written,str(exc)[:1000],{"collector":"cnmc_quarterly_load_v7","matched":matched,"skipped":skipped}); raise
