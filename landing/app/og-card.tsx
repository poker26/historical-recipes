// Shared card renderer for next/og — avatar + level-wreath crest + stats. Used by
// the profile OG image (1200×630 link unfurl) and the square share card (/card.png).
import { readFileSync, existsSync } from "fs";
import { join } from "path";
import { cap, pluralRu, type Profile } from "./lib";

const FONT_REG = readFileSync(join(process.cwd(), "public/og/pt-reg.ttf"));
const FONT_BOLD = readFileSync(join(process.cwd(), "public/og/pt-bold.ttf"));
export const CARD_FONTS = [
  { name: "PT", data: FONT_REG, weight: 400 as const, style: "normal" as const },
  { name: "PT", data: FONT_BOLD, weight: 700 as const, style: "normal" as const },
];

function dataUri(rel: string): string | null {
  const p = join(process.cwd(), rel);
  if (!existsSync(p)) return null;
  return "data:image/png;base64," + readFileSync(p).toString("base64");
}

/** Avatar disc framed by the level wreath (wreath has a transparent center). */
function Crest({ avatar, level, size }: { avatar?: string | null; level: number; size: number }) {
  const a = Math.round(size * 0.6);
  const avUri = avatar ? dataUri(`public/avatars/${avatar}.png`) : null;
  const wrUri = dataUri(`public/wreaths/${Math.min(5, Math.max(1, level))}.png`);
  return (
    <div style={{ position: "relative", width: size, height: size, display: "flex", alignItems: "center", justifyContent: "center" }}>
      {avUri ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={avUri} width={a} height={a} style={{ borderRadius: a / 2 }} alt="" />
      ) : (
        <div style={{ width: a, height: a, borderRadius: a / 2, background: "#cfe3cf", display: "flex" }} />
      )}
      {wrUri ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={wrUri} width={size} height={size} style={{ position: "absolute", top: 0, left: 0 }} alt="" />
      ) : null}
    </div>
  );
}

const GRAD = "linear-gradient(135deg, #eaf3de 0%, #f6fbef 55%, #ffffff 100%)";

/** Profile card element for the given variant. */
export function profileCard(p: Profile | null, variant: "og" | "square") {
  const nick = p?.nick ?? "Натуралист";
  const levelN = p?.level.n ?? 1;
  const levelTitle = cap(p?.level.title ?? "новичок");
  const species = p?.level.species ?? 0;
  const badges = p?.badges.length ?? 0;
  const stats = `${species} ${pluralRu(species, "вид", "вида", "видов")} · ${badges} ${pluralRu(badges, "значок", "значка", "значков")}`;

  const Brand = (
    <div style={{ display: "flex", alignItems: "center", fontSize: 30, color: "#1B5E20", fontWeight: 700 }}>
      <div style={{ width: 24, height: 24, borderRadius: 12, background: "#2E7D32", marginRight: 10 }} />
      Что растёт
    </div>
  );

  if (variant === "square") {
    return (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center",
        padding: 72, fontFamily: "PT", background: GRAD }}>
        {Brand}
        <Crest avatar={p?.avatar} level={levelN} size={560} />
        <div style={{ fontSize: 64, fontWeight: 700, color: "#1f2a24", marginTop: 8, textAlign: "center" }}>{nick}</div>
        <div style={{ fontSize: 40, color: "#2E7D32", fontWeight: 700, marginTop: 6 }}>{`${levelTitle} · уровень ${levelN}`}</div>
        <div style={{ fontSize: 30, color: "#3f4a43", marginTop: 6 }}>{stats}</div>
        <div style={{ flex: 1 }} />
        <div style={{ fontSize: 28, color: "#1B5E20", fontWeight: 700 }}>botanik.fun</div>
      </div>
    );
  }
  // og landscape
  return (
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", padding: 56, fontFamily: "PT", background: GRAD }}>
      <Crest avatar={p?.avatar} level={levelN} size={460} />
      <div style={{ display: "flex", flexDirection: "column", marginLeft: 48, flex: 1 }}>
        {Brand}
        <div style={{ fontSize: 28, color: "#5a6b5f", marginTop: 24 }}>Паспорт натуралиста</div>
        <div style={{ fontSize: 68, fontWeight: 700, color: "#1f2a24" }}>{nick}</div>
        <div style={{ fontSize: 38, color: "#2E7D32", fontWeight: 700, marginTop: 10 }}>{`${levelTitle} · уровень ${levelN}`}</div>
        <div style={{ fontSize: 28, color: "#3f4a43", marginTop: 6 }}>{stats}</div>
      </div>
    </div>
  );
}
