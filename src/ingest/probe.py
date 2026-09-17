from __future__ import annotations

from urllib.parse import urlparse

from .base import PipelineContext, finish_run, start_run

SOURCES = {
    "OFCOM_TELECOMS": ["https://www.ofcom.org.uk/phones-and-broadband/telecoms-infrastructure/telecommunications-market-data-update"],
    "CNMC_TELCO": ["https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/conjuntos-de-datos/datos-mensuales/telecomunicaciones"],
    "ARCEP_OBS": ["https://www.data.gouv.fr/api/1/datasets/observatoire-des-communications-electroniques/"],
    "BNetzA_TK": ["https://www.bundesnetzagentur.de/DE/Fachthemen/Telekommunikation/Marktdaten/start.html"],
    "AGCOM_OBS": ["https://www.agcom.it/pubblicazioni/osservatori"],
}


def probe(ctx: PipelineContext, source_code: str) -> dict:
    _, run_id = start_run(ctx, source_code, {"collector": "external_http_probe_v1"})
    results = []
    try:
        for url in SOURCES[source_code]:
            try:
                r = ctx.session.get(url, timeout=30, allow_redirects=True)
                results.append({"host": urlparse(url).netloc, "status": r.status_code,
                                "content_type": r.headers.get("content-type", ""), "bytes": len(r.content),
                                "final_url": r.url})
            except Exception as exc:
                results.append({"host": urlparse(url).netloc, "error": str(exc)[:500]})
        ok = any(200 <= x.get("status", 0) < 400 for x in results)
        finish_run(ctx, run_id, "success" if ok else "failed", len(results), 0,
                   None if ok else "No accessible endpoint", {"collector": "external_http_probe_v1", "probes": results})
        return {"source": source_code, "ok": ok, "probes": results}
    except Exception as exc:
        finish_run(ctx, run_id, "failed", 0, 0, str(exc)[:1000])
        raise
