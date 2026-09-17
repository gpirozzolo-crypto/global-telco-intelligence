"""Global Telco Intelligence external ingestion runner."""
import argparse
import os
from datetime import datetime, timezone

from supabase import create_client


def client():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def healthcheck():
    sb = client()
    result = sb.table("countries").select("iso3,name").eq("active", True).execute()
    print({"status": "ok", "countries": len(result.data), "checked_at": datetime.now(timezone.utc).isoformat()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="healthcheck")
    args = parser.parse_args()
    if args.source == "healthcheck":
        healthcheck()
    else:
        raise SystemExit(f"Collector not yet registered: {args.source}")


if __name__ == "__main__":
    main()
