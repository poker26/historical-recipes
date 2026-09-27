// Картинка страницы книги. Бэкенд отдаёт её только открытым книгам (остальным 403);
// первая отрисовка страницы из PDF занимает до нескольких секунд, поэтому getRaw ждёт 20 с.
import { isUuid } from "../../../../../../lib/api";
import { proxyImage } from "../../../../../../lib/api-library";

export const dynamic = "force-dynamic";

const SIZES = new Set(["thumb", "medium", "full"]);
const CACHE = "public, max-age=86400, stale-while-revalidate=604800";

export async function GET(req: Request, { params }: { params: { book: string; n: string } }) {
  const n = /^\d{1,5}$/.test(params.n) ? Number(params.n) : 0;
  if (!isUuid(params.book) || n < 1) return new Response(null, { status: 404 });
  const size = new URL(req.url).searchParams.get("size") || "medium";
  if (!SIZES.has(size)) return new Response(null, { status: 400 });
  return proxyImage(`/library/books/${params.book}/pages/${n}/image?size=${size}`, CACHE);
}
