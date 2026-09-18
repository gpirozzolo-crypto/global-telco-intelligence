export default async () => {
  const source = "https://www.ofcom.org.uk/siteassets/resources/documents/research-and-data/telecoms-research/telecoms-data-updates/telecommunications-market-data/telecommunications-market-data-update-q1-2026.csv?v=422841";
  const upstream = await fetch(source, { headers: {
    "User-Agent": "GlobalTelcoIntelligence/1.0 (+https://global-telco-intelligence.netlify.app)",
    "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8"
  }});
  if (!upstream.ok) return new Response("Ofcom upstream unavailable", { status: 502 });
  return new Response(await upstream.arrayBuffer(), { headers: {
    "Content-Type": upstream.headers.get("content-type") || "text/csv; charset=utf-8",
    "Cache-Control": "public, max-age=3600"
  }});
};
export const config = { path: "/data/ofcom-q1-2026.csv" };
