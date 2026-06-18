import type { Metadata } from "next";
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
      </body>
    </html>
  );
}
