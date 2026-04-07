import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Historical Recipes",
  description: "Platform for managing historical recipe books and herbal knowledge",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
