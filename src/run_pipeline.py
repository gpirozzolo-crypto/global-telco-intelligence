"""Global Telco Intelligence external ingestion runner."""
import argparse
import json
import os
from datetime import datetime, timezone

from supabase import create_client
from ingest.base import context
from ingest.probe import SOURCES, probe


def client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def healthcheck():
    sb = client()
    result = sb.table("countries").select("iso3,name").eq("active", True).execute()
    print({"status": "ok", "countries": len(result.data), "checked_at": datetime.now(timezone.utc).isoformat()})


def probes():
    ctx = context()
    failures = 0
    for code in SOURCES:
        result = probe(ctx, code)
        print(json.dumps(result))
        failures += int(not result["ok"])
    print(json.dumps({"probes_complete": len(SOURCES), "unreachable": failures}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="healthcheck", choices=["healthcheck", "probes"])
    args = parser.parse_args()
    healthcheck() if args.source == "healthcheck" else probes()


if __name__ == "__main__":
    main()
