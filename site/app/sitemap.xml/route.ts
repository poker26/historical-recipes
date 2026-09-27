import { sitemapIndex, sitemapNames } from "../../lib/sitemaps";

export const dynamic = "force-dynamic";

export async function GET() {
  const xml = sitemapIndex(await sitemapNames());
  return new Response(xml, {
    headers: { "content-type": "application/xml; charset=utf-8", "cache-control": "public, max-age=3600" },
  });
}
