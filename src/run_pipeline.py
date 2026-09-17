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
from ingest.probe import SOURCES, probe


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

def arcep_inspect():
    print(json.dumps({"source":"ARCEP_OBS",**inspect_arcep_workbooks(context())}))

def arcep_load():
    print(json.dumps({"source":"ARCEP_OBS",**load_arcep(context())}))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",default="healthcheck",choices=["healthcheck","probes","arcep","arcep-inspect","arcep-load"])
    args=parser.parse_args()
    {"healthcheck":healthcheck,"probes":probes,"arcep":arcep,"arcep-inspect":arcep_inspect,"arcep-load":arcep_load}[args.source]()

if __name__ == "__main__": main()
