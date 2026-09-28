// Картинка для соцсетей и мессенджеров по умолчанию: у главной, разделов и страниц
// без своей картинки (у карточки вида своя, с фото). Шрифт PT из public/og, как у
// карточек профиля: встроенный шрифт next/og кириллицу не рисует.
import { ImageResponse } from "next/og";
import { CARD_FONTS } from "./og-card";

export const alt = "Что растёт. Атлас растений и грибов, рецепты и библиотека травников";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between",
          background: "#2d5e48", color: "#fff8ec", padding: "72px 80px", fontFamily: "PT",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <svg width="72" height="72" viewBox="0 0 64 64" fill="none">
            <path d="M52 10C30 10 14 24 12 50c26 2 40-14 40-40Z" fill="#cfe3cf" />
            <path d="M14 50C24 38 34 28 50 12" stroke="#2d5e48" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
          <div style={{ fontSize: 56, fontWeight: 700 }}>Что растёт</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ fontSize: 58, fontWeight: 700, lineHeight: 1.15 }}>
            Атлас растений и грибов, домашние рецепты и библиотека травников
          </div>
          <div style={{ fontSize: 32, color: "#cfe3cf" }}>
            У каждого факта указаны книга, год и страница, с 1790 года
          </div>
        </div>
        <div style={{ fontSize: 30, color: "#cfe3cf" }}>botanik.fun</div>
      </div>
    ),
    { ...size, fonts: CARD_FONTS },
  );
}
