// Обложка книги: первая страница скана или PDF. Проксируем бэкенд, потому что MinIO и
// сам бэкенд снаружи не видны. Для закрытых книг бэкенд отвечает 403, его и отдаём.
import { isUuid } from "../../../../lib/api";
import { proxyImage } from "../../../../lib/api-library";

export const dynamic = "force-dynamic";

const SIZES = new Set(["thumb", "medium"]);
const CACHE = "public, max-age=604800, stale-while-revalidate=2592000";

export async function GET(req: Request, { params }: { params: { book: string } }) {
  if (!isUuid(params.book)) return new Response(null, { status: 404 });
  const size = new URL(req.url).searchParams.get("size") || "thumb";
  if (!SIZES.has(size)) return new Response(null, { status: 400 });
  return proxyImage(`/library/books/${params.book}/cover?size=${size}`, CACHE);
}
