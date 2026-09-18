"""Global Telco Intelligence external ingestion runner."""
import argparse
import json
import os
from datetime import datetime, timezone

from supabase import create_client
from ingest.arcep import discover as discover_arcep
from ingest.arcep_inspect import inspect_arcep_workbooks
from ingest.arcep_load import load_arcep
from ingest.base import context
from ingest.cnmc_load import load_cnmc
from ingest.ofcom_load import load_ofcom
from ingest.probe import SOURCES, probe
from ingest.regulator_inspect import inspect_agcom, inspect_bnetza, inspect_cnmc
from ingest.regulator_load import load_agcom, load_bnetza


def client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def healthcheck():
    sb = client(); result = sb.table("countries").select("iso3,name").eq("active", True).execute()
    print({"status":"ok","countries":len(result.data),"checked_at":datetime.now(timezone.utc).isoformat()})

def probes():
    ctx=context(); failures=0
    for code in SOURCES:
        result=probe(ctx,code); print(json.dumps(result)); failures += int(not result["ok"])
    print(json.dumps({"probes_complete":len(SOURCES),"unreachable":failures}))

def arcep():
    result=discover_arcep(context()); print(json.dumps({"source":"ARCEP_OBS","resources":len(result["resources"]),"dataset_last_modified":result["dataset_last_modified"]}))

def arcep_inspect(): print(json.dumps({"source":"ARCEP_OBS",**inspect_arcep_workbooks(context())}))
def arcep_load(): print(json.dumps({"source":"ARCEP_OBS",**load_arcep(context())}))
def agcom_inspect(): print(json.dumps({"source":"AGCOM_OBS",**inspect_agcom(context())}))
def cnmc_inspect(): print(json.dumps({"source":"CNMC_TELCO",**inspect_cnmc(context())}))
def cnmc_load(): print(json.dumps({"source":"CNMC_TELCO",**load_cnmc(context())}))
def bnetza_inspect(): print(json.dumps({"source":"BNetzA_TK",**inspect_bnetza(context())}))
def agcom_load(): print(json.dumps({"source":"AGCOM_OBS",**load_agcom(context())}))
def bnetza_load(): print(json.dumps({"source":"BNetzA_TK",**load_bnetza(context())}))
def ofcom_load(): print(json.dumps({"source":"OFCOM_TELECOMS",**load_ofcom(context())}))

def calculated():
    sb = client()
    result = sb.rpc("refresh_calculated_kpis").execute()
    print(json.dumps({"stage":"calculated-kpis","result":result.data}, default=str))

def quality():
    sb = client()
    result = sb.rpc("run_observation_quality_checks").execute()
    print(json.dumps({"stage":"quality-checks","new_issues":result.data}, default=str))

def main():
    parser=argparse.ArgumentParser()
    funcs={"healthcheck":healthcheck,"probes":probes,"arcep":arcep,"arcep-inspect":arcep_inspect,"arcep-load":arcep_load,
           "agcom-inspect":agcom_inspect,"cnmc-inspect":cnmc_inspect,"cnmc-load":cnmc_load,"bnetza-inspect":bnetza_inspect,
           "agcom-load":agcom_load,"bnetza-load":bnetza_load,"ofcom-load":ofcom_load,"calculated":calculated,"quality":quality}
    parser.add_argument("--source",default="healthcheck",choices=list(funcs))
    args=parser.parse_args(); funcs[args.source]()

if __name__ == "__main__": main()
