// Shared constants for nastoiki.pro.

// Backend base URL. Empty string = same-origin (prod: nginx proxies /api →
// backend under nastoiki.pro). Override for local dev via NEXT_PUBLIC_API_BASE
// (e.g. http://localhost:8000).
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

export const QTICKETS_URL = "https://pronino.qtickets.ru";
export const CHANNEL_URL = "https://t.me/neprostoynastoy";
export const MAX_CHANNEL_URL = "https://max.ru/join/88A7-dwqfj99uKSBbAYFc30JXT1RBizc4R-9nlEC0LQ";
export const TG_PERSONAL = "https://t.me/hippo26";
export const MAX_PHONE = "+7 925 505-26-26";
export const BOT_URL = "https://t.me/neprostoinastoi_bot";
export const GIFT_BOOK_URL = `${BOT_URL}?start=book1858`;
export const MAX_BOT_URL = "https://max.ru/se13474809_bot";

// Yandex.Metrika counter id (loaded in layout, production only).
export const YM_ID = 110331711;

/** Append UTM tags to a qtickets link so orders are attributable to the site
 *  and to the specific placement (hero / date chip / mk button / agent). */
export function qt(url: string, medium: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}utm_source=nastoiki.pro&utm_medium=${medium}`;
}

/** Fire a named Metrika goal (no-op outside production / before the tag loads). */
export function ymGoal(goal: string) {
  try {
    const w = window as any;
    if (typeof w.ym === "function") w.ym(YM_ID, "reachGoal", goal);
  } catch { /* analytics must never break the page */ }
}

// Opening line and a few starter prompts that showcase the recipe-first flow.
export const GREETING =
  "Здравствуйте! Я мастер старинных русских настоек: горьких, наливок, бальзамов и " +
  "хлебных дистиллятов. Расскажу вам рецепт по дореволюционным книгам, объясню, зачем в " +
  "нём нужен каждый корешок, с чем его лучше сочетать и когда собирать травы. С чего " +
  "начнём? 😊";

export const SUGGESTS: string[] = [
  "Расскажи рецепт английской горькой",
  "Что за настойки делают из дягиля?",
  "С чем сочетается корень калгана?",
  "Что собрать для наливки рядом со мной сейчас?",
];
