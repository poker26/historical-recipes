import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { YM_ID } from "./lib";

const SITE = "https://nastoiki.pro";
const DESC =
  "Старинные русские настойки, наливки и бальзамы по дореволюционным рецептам. " +
  "Спросите мастера: что за рецепт, зачем каждый корешок, с чем сочетать и когда собирать. " +
  "Мастер-классы по настойкам в Пронино под Серпуховом.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: {
    default: "Настойки.pro — старинные русские настойки и мастер-классы",
    template: "%s · Настойки.pro",
  },
  description: DESC,
  keywords: [
    "настойки по старинным рецептам", "старинные русские настойки", "мастер-класс настойки",
    "мастер-класс настойки Москва", "дореволюционные рецепты настоек", "горькая настойка рецепт",
    "наливки бальзамы тинктуры", "как сделать настойку", "рецепты настоек на травах", "Пронино мастер-класс",
  ],
  alternates: { canonical: SITE },
  robots: { index: true, follow: true },
  openGraph: {
    type: "website",
    siteName: "Настойки.pro",
    locale: "ru_RU",
    url: SITE,
    title: "Настойки.pro — искусство никуда не спешить",
    description: DESC,
    images: [{ url: "/img/og.jpg", width: 1536, height: 1024, alt: "Старинные русские настойки" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Настойки.pro — искусство никуда не спешить",
    description: DESC,
    images: ["/img/og.jpg"],
  },
  // Fill via container env once registered in the webmaster panels (or verify
  // Yandex through the existing Metrika counter — no meta tag needed there).
  verification: {
    yandex: process.env.YANDEX_VERIFICATION || undefined,
    google: process.env.GOOGLE_VERIFICATION || undefined,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        {children}

        {/* Yandex.Metrika counter — production only (skip in dev/preview). */}
        {process.env.NODE_ENV === "production" && (
          <>
            <Script id="yandex-metrika" strategy="afterInteractive">
              {`(function(m,e,t,r,i,k,a){
                  m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
                  m[i].l=1*new Date();
                  for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
                  k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
              })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=${YM_ID}', 'ym');
              ym(${YM_ID}, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});`}
            </Script>
            <noscript>
              <div>
                <img src={`https://mc.yandex.ru/watch/${YM_ID}`} style={{ position: "absolute", left: "-9999px" }} alt="" />
              </div>
            </noscript>
          </>
        )}
      </body>
    </html>
  );
}
