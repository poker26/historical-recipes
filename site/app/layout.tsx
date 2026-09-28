import type { Metadata } from "next";
import Script from "next/script";
import { Literata, Golos_Text } from "next/font/google";
import "./globals.css";

// Та же пара шрифтов, что в приложении (docs/design-v2/DECISION.md): сайт —
// продолжение продукта, а не отдельная страница. Оба свободные, с кириллицей.
const literata = Literata({ subsets: ["cyrillic", "latin"], weight: ["600"], style: ["normal", "italic"], variable: "--font-literata", display: "swap" });
const golos = Golos_Text({ subsets: ["cyrillic", "latin"], weight: ["400", "500", "600"], variable: "--font-golos", display: "swap" });

const SITE = "https://botanik.fun";

// Все страницы рендерятся на запрос. Иначе `next build` в Docker запекает их статически,
// а бэкенд из сборки недоступен: в HTML навсегда уезжают пустые списки. Данные при этом
// кэшируются: в Next 14.2 force-dynamic не отключает кэш fetch с явным `next.revalidate`,
// поэтому каждый getJson(path, revalidate) отдаётся из кэша данных, а не ходит в бэкенд.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: {
    default: "Что растёт. Атлас растений и грибов, рецепты и библиотека травников",
    template: "%s · Что растёт",
  },
  description:
    "Справочник по растениям и грибам, собранный из травников, лечебников и поваренных книг с 1790 года. В нём применение, состав и сбор растений, домашние рецепты и сканы страниц самих книг.",
  openGraph: {
    type: "website",
    siteName: "Что растёт",
    locale: "ru_RU",
    url: SITE,
    images: [{ url: `${SITE}/opengraph-image`, width: 1200, height: 630 }],
  },
  twitter: { card: "summary_large_image" },
  // Коды подтверждения прав из Яндекс Вебмастера и Google Search Console приходят из
  // окружения контейнера: получил код в кабинете, прописал переменную, пересоздал site.
  verification: {
    yandex: process.env.YANDEX_VERIFICATION || undefined,
    google: process.env.GOOGLE_SITE_VERIFICATION || undefined,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${literata.variable} ${golos.variable}`}>
      <body>
        {/* Шапка рисуется страницами (им известен активный раздел); контейнер общий. */}
        <div className="container">{children}</div>
        {/* Yandex.Metrika counter */}
        <Script id="yandex-metrika" strategy="afterInteractive" dangerouslySetInnerHTML={{ __html: `
          (function(m,e,t,r,i,k,a){
              m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
              m[i].l=1*new Date();
              for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
              k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
          })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=110386263', 'ym');
          ym(110386263, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
        ` }} />
        <noscript><div><img src="https://mc.yandex.ru/watch/110386263" style={{ position: "absolute", left: "-9999px" }} alt="" /></div></noscript>
        {/* /Yandex.Metrika counter */}
      </body>
    </html>
  );
}
