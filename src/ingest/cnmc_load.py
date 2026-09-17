from __future__ import annotations

import re
from datetime import date

from .base import PipelineContext, finish_run, one, start_run, utcnow

DATASTORE = "https://catalogodatos.cnmc.es/api/3/action/datastore_search"
RESOURCE = "8ea25e53-b955-4a42-bca4-0a7183237844"
SOURCE_URL = "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/datos-trimestrales/datos-de-mercados/telecomunicaciones-3"

# Deliberately conservative: only unambiguous country totals are normalized.
RULES = [
    ("MOBILE_SUBS", "Telefonía móvil", "Líneas", None, "lineas_o_accesos"),
    ("FIXED_BB_SUBS", "Banda ancha fija minorista", "Líneas", None, "lineas_o_accesos"),
    ("FTTH_SUBS", "Banda ancha fija minorista", "Líneas", "FTTH", "lineas_o_accesos"),
    ("FTTH_HOMES_PASSED", "Red de distribución", "Accesos", "FTTH", "lineas_o_accesos"),
    ("MOBILE_REVENUE", "Telefonía móvil", "Ingresos", None, "ingresos"),
    ("FIXED_REVENUE", "Banda ancha fija minorista", "Ingresos", None, "ingresos"),
]

DIMENSIONS = ["tipo_de_mercado","tipo_de_cliente","segmento","tipo_de_trafico","tipo_de_contrato","tipo_de_linea",
              "tipo_de_mensaje","tipo_de_trafico_de_mensaje","velocidad_baf","tipo_de_oferta","tipo_de_tarifa",
              "tipo_de_ce_minorista","tipo_de_circuito","tipo_de_emision","tipo_de_operador","tipo_de_medio",
              "tipo_de_publicidad","tipo_de_contratacion","tipo_servicio_audiovisual_mayorista","tipo_de_ba_may",
              "tipo_de_interconexion","tipo_de_tarificacion_en_interconexion","tipo_de_ambito"]

def _na(v): return v in (None, "", "N/A")

def _period(s):
    m=re.fullmatch(r"(20\d{2})T([1-4])", str(s or ""))
    if not m: return None
    y,q=int(m.group(1)),int(m.group(2)); month=q*3; day=(31,30,30,31)[q-1]
    return date(y,month,day).isoformat()

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

def _is_total(r, tech):
    if not _na(r.get("operador")): return False
    if tech is None and not _na(r.get("tecnologia_de_acceso")): return False
    if tech is not None and r.get("tecnologia_de_acceso") != tech: return False
    # FTTH homes passed must be installed access; other dimensions must be aggregate.
    if r.get("servicio")=="Red de distribución" and r.get("tipo_de_acceso_de_infraestructuras") not in ("Acceso instalado",None,"N/A"): return False
    return all(_na(r.get(d)) for d in DIMENSIONS)

def load_cnmc(ctx: PipelineContext) -> dict:
    source_id,run_id=start_run(ctx,"CNMC_TELCO",{"collector":"cnmc_quarterly_load_v1"})
    country=one(ctx.db,"countries","iso3","ESP")
    kpis={c:one(ctx.db,"kpis","code",c) for c,_,_,_,_ in RULES}
    read=written=0; matched={}
    try:
        records=[]; offset=0
        while True:
            r=ctx.session.get(DATASTORE,params={"resource_id":RESOURCE,"limit":1000,"offset":offset},timeout=60); r.raise_for_status()
            result=r.json()["result"]; batch=result.get("records",[]); records.extend(batch)
            if len(records)>=result.get("total",0) or not batch: break
            offset += len(batch)
        read=len(records)
        for rec in records:
            period=_period(rec.get("trimestre"))
            if not period: continue
            for code,service,concept,tech,field in RULES:
                if rec.get("servicio")!=service or rec.get("concepto")!=concept or not _is_total(rec,tech): continue
                val=rec.get(field)
                if val is None: continue
                try: val=float(val)
                except (TypeError,ValueError): continue
                unit=rec.get("unidades") or kpis[code]["unit"]
                numeric=val*1_000_000 if "Millones de euros" in str(unit) else val
                indicator=f"{service} | {concept}" + (f" | {tech}" if tech else "")
                raw={"ingestion_run_id":run_id,"source_id":source_id,"country_id":country["id"],"operator_id":None,
                     "source_indicator":indicator,"period_date":period,"frequency":"quarterly","value_text":str(rec.get(field)),
                     "value_numeric":val,"unit_raw":unit,"currency_raw":"EUR" if "euros" in str(unit).lower() else None,
                     "source_url":SOURCE_URL,"retrieved_at":utcnow(),"payload":{"resource_id":RESOURCE,"record":rec},
                     "source_record_key":f"{RESOURCE}:{rec.get('_id')}:{code}"}
                raw_id=_upsert_raw(ctx,raw)
                obs={"kpi_id":kpis[code]["id"],"country_id":country["id"],"operator_id":None,"period_date":period,
                     "frequency":"quarterly","value":numeric,"unit":kpis[code]["unit"],
                     "currency_code":"EUR" if "REVENUE" in code else None,"source_id":source_id,"raw_observation_id":raw_id,
                     "definition_version":kpis[code]["definition_version"],"quality_flag":"ok","retrieved_at":utcnow(),
                     "quality_notes":f"CNMC country total: {indicator}"}
                _upsert_obs(ctx,obs); written+=1; matched[code]=matched.get(code,0)+1
        meta={"collector":"cnmc_quarterly_load_v1","resource_id":RESOURCE,"matched":matched}
        finish_run(ctx,run_id,"success",read,written,metadata=meta)
        ctx.db.table("pipeline_state").upsert({"source_id":source_id,"last_success_at":utcnow(),"last_attempt_at":utcnow(),"cursor_state":meta}).execute()
        return {"rows_read":read,"rows_written":written,"matched":matched}
    except Exception as exc:
        finish_run(ctx,run_id,"failed",read,written,str(exc)[:1000],{"collector":"cnmc_quarterly_load_v1","matched":matched}); raise
