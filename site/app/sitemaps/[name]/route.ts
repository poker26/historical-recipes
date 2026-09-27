import { sitemapEntries, urlset } from "../../../lib/sitemaps";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: { name: string } }) {
  const name = params.name.replace(/\.xml$/, "");
  const entries = await sitemapEntries(name);
  if (!entries) return new Response("Not Found", { status: 404 });
  return new Response(urlset(entries), {
    headers: { "content-type": "application/xml; charset=utf-8", "cache-control": "public, max-age=3600" },
  });
}
