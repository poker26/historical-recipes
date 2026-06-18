import { readFileSync } from "fs";
import { join } from "path";
import { ImageResponse } from "next/og";
import { getProfile, cap, pluralRu, tierColors } from "../../lib";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Паспорт натуралиста — Что растёт";

// Read from public/ (copied into the standalone runtime by the Dockerfile, and
// present in dev) — `fetch(new URL(...,import.meta.url))` fails at runtime because
// import.meta.url resolves to a relative /_next path Node's fetch can't parse.
const FONT_REG = readFileSync(join(process.cwd(), "public/og/pt-reg.ttf"));
const FONT_BOLD = readFileSync(join(process.cwd(), "public/og/pt-bold.ttf"));

type Params = { params: { id: string } };

/** Drawn medallion — pure divs (the Cyrillic font subset has no emoji/star glyphs).
 *  Metal ring by tier + a green centre. */
function Medal({ tier, earned }: { tier: number; earned: boolean }) {
  const [light, metal] = tierColors(tier);
  return (
    <div
      style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        width: 120, height: 120, borderRadius: 60,
        background: earned ? light : "#EFEFEC",
        border: `10px solid ${earned ? metal : metal + "55"}`,
        opacity: earned ? 1 : 0.5,
      }}
    >
      <div style={{ width: 48, height: 48, borderRadius: 24, background: "#2E7D32" }} />
    </div>
  );
}

export default async function Image({ params }: Params) {
  const p = await getProfile(params.id);
  const nick = p?.nick ?? "Натуралист";
  const levelN = p?.level.n ?? 1;
  const levelTitle = cap(p?.level.title ?? "новичок");
  const species = p?.level.species ?? 0;
  const badges = p?.badges.length ?? 0;
  const top = Math.max(1, ...((p?.badges ?? []).map((b) => b.tier)), 0);

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%", height: "100%", display: "flex", flexDirection: "column",
          padding: 64, fontFamily: "PT",
          background: "linear-gradient(135deg, #eaf3de 0%, #f6fbef 55%, #ffffff 100%)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", fontSize: 32, color: "#1B5E20", fontWeight: 700 }}>
          <div style={{ width: 26, height: 26, borderRadius: 13, background: "#2E7D32", marginRight: 12 }} />
          Что растёт
        </div>

        <div style={{ display: "flex", flexDirection: "column", marginTop: 36, flex: 1 }}>
          <div style={{ fontSize: 30, color: "#5a6b5f" }}>Паспорт натуралиста</div>
          <div style={{ fontSize: 72, fontWeight: 700, color: "#1f2a24", marginTop: 6 }}>{nick}</div>
          <div style={{ fontSize: 40, color: "#2E7D32", fontWeight: 700, marginTop: 12 }}>
            {`${levelTitle} · уровень ${levelN}`}
          </div>
          <div style={{ fontSize: 30, color: "#3f4a43", marginTop: 8 }}>
            {`${species} ${pluralRu(species, "вид", "вида", "видов")} · ${badges} ${pluralRu(badges, "значок", "значка", "значков")}`}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", gap: 22 }}>
            <Medal tier={1} earned={top >= 1 && badges > 0} />
            <Medal tier={2} earned={top >= 2} />
            <Medal tier={3} earned={top >= 3} />
          </div>
          <div style={{ fontSize: 30, color: "#1B5E20", fontWeight: 700, display: "flex" }}>botanik.fun</div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "PT", data: FONT_REG, weight: 400, style: "normal" },
        { name: "PT", data: FONT_BOLD, weight: 700, style: "normal" },
      ],
    },
  );
}
