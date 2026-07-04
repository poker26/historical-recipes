import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";

const SITE = "https://botanik.fun";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: {
    default: "Что растёт — определяй растения, проходи квесты, собирай значки",
    template: "%s · Что растёт",
  },
  description:
    "Полевой определитель растений с квестами по местам и сезонам. Фотографируй, узнавай, собирай значки натуралиста.",
  openGraph: {
    type: "website",
    siteName: "Что растёт",
    locale: "ru_RU",
    url: SITE,
  },
  twitter: { card: "summary_large_image" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
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
