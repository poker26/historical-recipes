// robots.txt своим обработчиком, а не через app/robots.ts: генератор Next не умеет
// Clean-param, а Яндекс по нему склеивает адреса с параметрами, которые не меняют
// страницу (вкладки рецептов на карточке, подсветка цитаты, метки рекламы).
export const dynamic = "force-static";

const BODY = `User-agent: *
Allow: /
Disallow: /i/
Disallow: /search
Disallow: /p/*/card.png
Disallow: /plant/*/card.png
Disallow: /collection
Clean-param: kind /atlas/
Clean-param: hl /library/
Clean-param: utm_source&utm_medium&utm_campaign&utm_content&utm_term&yclid&gclid&fbclid&from

Sitemap: https://botanik.fun/sitemap.xml
`;

export function GET() {
  return new Response(BODY, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=3600" } });
}
