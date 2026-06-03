"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, PlantDetail } from "@/lib/api";

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <h3 style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        {title}
        {count !== undefined && count > 0 && <span className="badge badge-blue">{count}</span>}
      </h3>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <span style={{ marginRight: 12 }}>
      <span style={{ color: "var(--text-muted)" }}>{label}:</span> {value}
    </span>
  );
}

function Original({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic", marginTop: 6, borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
      {text}
    </div>
  );
}

export default function PlantDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [plant, setPlant] = useState<PlantDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getPlant(id).then(setPlant).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!plant) return <div className="empty">Plant not found</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/plants" style={{ color: "var(--text-muted)", fontSize: 13 }}>Herbarium /</Link>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {plant.name}
            {plant.is_toxic && <span className="badge badge-red">⚠ toxic</span>}
          </h1>
        </div>
      </div>

      {/* Identity */}
      <div className="card" style={{ marginBottom: 20, display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        {plant.photo_url && (
          <figure style={{ margin: 0, width: 240, flexShrink: 0 }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={plant.photo_url}
              alt={plant.name}
              style={{ width: "100%", borderRadius: 8, display: "block" }}
            />
            {plant.photo_attribution && (
              <figcaption style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                {plant.photo_attribution}
                {plant.photo_source === "inaturalist" && " · iNaturalist"}
              </figcaption>
            )}
          </figure>
        )}
        <div style={{ flex: 1, minWidth: 280 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, fontSize: 14 }}>
            <div><span style={{ color: "var(--text-muted)" }}>Latin:</span> <em>{plant.name_latin || "—"}</em></div>
            <div>
              <span style={{ color: "var(--text-muted)" }}>Family:</span>{" "}
              {plant.family || plant.family_latin
                ? `${plant.family || ""}${plant.family && plant.family_latin ? " · " : ""}${plant.family_latin || ""}`
                : "—"}
            </div>
            <div>
              <span style={{ color: "var(--text-muted)" }}>Parts used:</span>{" "}
              {plant.parts_used?.length ? plant.parts_used.map((p) => (
                <span key={p} className="badge badge-green" style={{ marginLeft: 4 }}>{p}</span>
              )) : "—"}
            </div>
            <div>
              <span style={{ color: "var(--text-muted)" }}>Historical names:</span>{" "}
              {plant.names_historical?.join(", ") || "—"}
            </div>
          </div>
          {plant.description && (
            <div style={{ marginTop: 14, fontSize: 14, lineHeight: 1.55 }}>{plant.description}</div>
          )}
        </div>
      </div>

      {/* Cross-domain link: recipes that use this plant */}
      {plant.recipes && plant.recipes.length > 0 && (
        <Section title="Used in recipes" count={plant.recipes.length}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {plant.recipes.map((r) => (
              <div key={r.id} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 14 }}>
                <Link href={`/recipes/${r.id}`} style={{ fontWeight: 500 }}>{r.name}</Link>
                {r.category && <span className="badge badge-blue">{r.category}</span>}
                {r.book && (
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    {r.book}{r.year ? ` · ${r.year}` : ""}
                  </span>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Toxicity — surfaced first when present */}
      {plant.toxicities.length > 0 && (
        <Section title="⚠ Toxicity" count={plant.toxicities.length}>
          {plant.toxicities.map((t) => (
            <div key={t.id} style={{ padding: "10px 0", borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 14 }}>
                <Field label="Severity" value={t.severity} />
                <Field label="Toxic parts" value={t.toxic_parts?.join(", ")} />
              </div>
              {t.symptoms && <div style={{ fontSize: 14, marginTop: 4 }}><span style={{ color: "var(--text-muted)" }}>Symptoms:</span> {t.symptoms}</div>}
              {t.antidote && <div style={{ fontSize: 14, marginTop: 4 }}><span style={{ color: "var(--text-muted)" }}>Antidote:</span> {t.antidote}</div>}
              <Original text={t.original_text} />
              {t.source && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>— {t.source}</div>}
            </div>
          ))}
        </Section>
      )}

      {/* Medicinal uses */}
      <Section title="Medicinal uses" count={plant.medicinal_uses.length}>
        {plant.medicinal_uses.length === 0 ? (
          <div className="empty">No medicinal uses extracted yet</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Action</th>
                  <th>Part</th>
                  <th>Indications</th>
                  <th>Preparation</th>
                  <th>Dosage</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {plant.medicinal_uses.map((u) => (
                  <tr key={u.id}>
                    <td>
                      {u.action || "—"}
                      {u.action_system && <span className="badge badge-blue" style={{ marginLeft: 6 }}>{u.action_system}</span>}
                    </td>
                    <td>{u.part || "—"}</td>
                    <td>{u.indications || "—"}{u.contraindications && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 3 }}>✗ {u.contraindications}</div>}</td>
                    <td>{u.preparation || "—"}</td>
                    <td>{u.dosage || "—"}</td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{u.source || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Chemical composition */}
      {plant.compounds.length > 0 && (
        <Section title="Chemical composition" count={plant.compounds.length}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {plant.compounds.map((c) => (
              <div key={c.id} className="badge badge-green" style={{ padding: "6px 10px" }} title={[c.compound_group, c.part, c.notes].filter(Boolean).join(" · ")}>
                {c.compound}
                {c.part && <span style={{ opacity: 0.7 }}> ({c.part})</span>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Harvest */}
      {plant.harvests.length > 0 && (
        <Section title="Collection & harvest" count={plant.harvests.length}>
          {plant.harvests.map((h) => (
            <div key={h.id} style={{ padding: "10px 0", borderTop: "1px solid var(--border)", fontSize: 14 }}>
              <Field label="Part" value={h.part} />
              <Field label="Season" value={h.season} />
              {h.method && <div style={{ marginTop: 4 }}>{h.method}</div>}
              <Original text={h.original_text} />
              {h.source && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>— {h.source}</div>}
            </div>
          ))}
        </Section>
      )}

      {/* Habitat */}
      {plant.habitats.length > 0 && (
        <Section title="Habitat & range" count={plant.habitats.length}>
          {plant.habitats.map((h) => (
            <div key={h.id} style={{ padding: "10px 0", borderTop: "1px solid var(--border)", fontSize: 14 }}>
              <Field label="Region" value={h.region} />
              <Field label="Biotope" value={h.biotope} />
              {h.status && <span className="badge badge-yellow">{h.status}</span>}
              <Original text={h.original_text} />
              {h.source && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>— {h.source}</div>}
            </div>
          ))}
        </Section>
      )}

      {/* Sources */}
      {plant.mentions.length > 0 && (
        <Section title="Sources" count={plant.mentions.length}>
          <div style={{ fontSize: 14 }}>
            {plant.mentions.map((m) => (
              <div key={m.id} style={{ padding: "6px 0" }}>
                {m.book || "—"}
                {m.original_name && <span style={{ color: "var(--text-muted)" }}> · as “{m.original_name}”</span>}
                {m.page_number != null && <span style={{ color: "var(--text-muted)" }}> · p. {m.page_number}</span>}
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}
