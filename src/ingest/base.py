from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from supabase import Client, create_client


@dataclass
class PipelineContext:
    db: Client
    session: requests.Session


def context() -> PipelineContext:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    session = requests.Session()
    session.headers.update({"User-Agent": "global-telco-intelligence/0.1 (+GitHub Actions)"})
    return PipelineContext(create_client(url, key), session)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def one(db: Client, table: str, column: str, value: Any) -> dict:
    rows = db.table(table).select("*").eq(column, value).limit(1).execute().data
    if not rows:
        raise RuntimeError(f"Missing {table}.{column}={value}")
    return rows[0]


def start_run(ctx: PipelineContext, source_code: str, metadata: dict | None = None) -> tuple[int, str]:
    source = one(ctx.db, "sources", "code", source_code)
    row = ctx.db.table("ingestion_runs").insert({
        "source_id": source["id"], "status": "running", "metadata": metadata or {}
    }).execute().data[0]
    return source["id"], row["id"]


def finish_run(ctx: PipelineContext, run_id: str, status: str, rows_read: int, rows_written: int,
               error: str | None = None, metadata: dict | None = None) -> None:
    payload = {"status": status, "finished_at": utcnow(), "rows_read": rows_read, "rows_written": rows_written,
               "error_message": error}
    if metadata is not None:
        payload["metadata"] = metadata
    ctx.db.table("ingestion_runs").update(payload).eq("id", run_id).execute()
