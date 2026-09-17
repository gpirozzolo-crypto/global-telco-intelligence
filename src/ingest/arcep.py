from __future__ import annotations

from .base import PipelineContext, finish_run, start_run

DATASET_API = "https://www.data.gouv.fr/api/1/datasets/observatoire-des-communications-electroniques/"


def discover(ctx: PipelineContext) -> dict:
    """Discover ARCEP official dataset resources and persist a reproducible manifest."""
    _, run_id = start_run(ctx, "ARCEP_OBS", {"collector": "arcep_discovery_v1"})
    try:
        r = ctx.session.get(DATASET_API, timeout=45)
        r.raise_for_status()
        dataset = r.json()
        resources = []
        for item in dataset.get("resources", []):
            resources.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "format": (item.get("format") or "").lower(),
                "url": item.get("url"),
                "latest": item.get("latest"),
                "last_modified": item.get("last_modified"),
                "filesize": item.get("filesize"),
                "type": item.get("type"),
            })
        downloadable = [x for x in resources if x["url"] and x["format"] in {"xlsx", "xls", "csv"}]
        meta = {
            "collector": "arcep_discovery_v1",
            "dataset_id": dataset.get("id"),
            "dataset_title": dataset.get("title"),
            "dataset_last_modified": dataset.get("last_modified"),
            "resources": downloadable,
        }
        finish_run(ctx, run_id, "success", len(resources), 0, metadata=meta)
        return meta
    except Exception as exc:
        finish_run(ctx, run_id, "failed", 0, 0, str(exc)[:1000])
        raise
