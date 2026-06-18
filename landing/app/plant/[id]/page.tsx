import type { Metadata } from "next";
import { getPlant, quoteText } from "../../lib";
import { Header, Footer, DownloadButtons } from "../../ui";

type Params = { params: { id: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const p = await getPlant(params.id);
  if (!p?.name) return { title: "Растение" };
  const lead = quoteText(p.lead_fact) || quoteText(p.fun_fact) || p.verdict || undefined;
  const title = `${p.name} — определено в «Что растёт»`;
  return {
    title,
    description: lead || `Что это за растение? Узнай в приложении «Что растёт».`,
    openGraph: {
      title,
      description: lead || "Я определил это растение в «Что растёт» 🌿",
      type: "article",
      images: p.photo_url ? [{ url: p.photo_url }] : undefined,
    },
    twitter: { card: p.photo_url ? "summary_large_image" : "summary", title, description: lead || "" },
  };
}

export default async function PlantPage({ params }: Params) {
  const p = await getPlant(params.id);
  const lead = p ? quoteText(p.lead_fact) || quoteText(p.fun_fact) : null;
  const uses = (p?.uses ?? []).filter((u) => u.action).slice(0, 5);

  return (
    <>
      <Header />

      {!p?.name ? (
        <section className="card" style={{ marginTop: 24, textAlign: "center" }}>
          <h1>Растение не найдено</h1>
          <p style={{ color: "#6b7280" }}>Возможно, ссылка устарела.</p>
        </section>
      ) : (
        <>
          <section style={{ marginTop: 16, display: "grid", gridTemplateColumns: p.photo_url ? "260px 1fr" : "1fr", gap: 24 }}>
            {p.photo_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={p.photo_url}
                alt={p.name}
                style={{ width: "100%", height: 260, objectFit: "cover", borderRadius: 16 }}
              />
            ) : null}
            <div>
              <span className="chip">🌿 Определено в «Что растёт»</span>
              <h1 style={{ fontSize: 32, margin: "12px 0 4px" }}>{p.name}</h1>
              {p.name_latin ? (
                <div style={{ fontStyle: "italic", color: "#6b7280", fontSize: 16 }}>{p.name_latin}</div>
              ) : null}
              {p.family ? <div style={{ color: "#9ca3af", fontSize: 14, marginTop: 2 }}>Семейство: {p.family}</div> : null}
              {p.is_toxic ? (
                <div style={{ marginTop: 12, padding: "8px 12px", borderRadius: 10, background: "#FFEBEE", color: "#B71C1C", fontWeight: 600, fontSize: 14, display: "inline-block" }}>
                  ⚠️ Ядовитое растение
                </div>
              ) : null}
              {p.verdict ? <p style={{ fontSize: 16, color: "#3f4a43", marginTop: 12 }}>{p.verdict}</p> : null}
            </div>
          </section>

          {lead ? (
            <section className="card" style={{ marginTop: 24, background: "#f6fbef", border: "none" }}>
              <p style={{ margin: 0, fontSize: 16, lineHeight: 1.5 }}>{lead}</p>
            </section>
          ) : null}

          {uses.length ? (
            <section style={{ marginTop: 24 }}>
              <h2 className="section-title">Чем известно</h2>
              <div className="card">
                {uses.map((u, i) => (
                  <div key={i} style={{ padding: "8px 0", borderBottom: i < uses.length - 1 ? "1px solid #eef0ea" : "none" }}>
                    <b>{u.action}</b>
                    {u.summary ? <div style={{ fontSize: 14, color: "#6b7280", marginTop: 2 }}>{u.summary}</div> : null}
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <section className="card" style={{ marginTop: 24, textAlign: "center", background: "#eaf3de", border: "none" }}>
            <h2 style={{ margin: "4px 0 8px" }}>Определи растение сам 🌿</h2>
            <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 440 }}>
              Сфотографируй любое растение — «Что растёт» определит вид и расскажет о нём.
            </p>
            <div style={{ display: "flex", justifyContent: "center" }}>
              <DownloadButtons />
            </div>
          </section>
        </>
      )}

      <Footer />
    </>
  );
}
