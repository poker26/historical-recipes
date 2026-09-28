// Ключ IndexNow. Яндекс скачивает этот файл (keyLocation в запросе) и так проверяет, что
// адреса страниц присылает владелец сайта. Ключ приходит из окружения контейнера
// (INDEXNOW_KEY в .env); без него адреса не существует. Отправка: backend/scripts/indexnow_submit.py.
export const dynamic = "force-dynamic";

export function GET() {
  const key = process.env.INDEXNOW_KEY?.trim();
  if (!key) return new Response("Not Found", { status: 404 });
  return new Response(key, {
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=86400" },
  });
}
