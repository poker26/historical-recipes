"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, PlantDetail, GenusCompound } from "@/lib/api";

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

function RecipesSection({ recipes }: { recipes: NonNullable<PlantDetail["recipes"]> }) {
  return (
    <Section title="Used in recipes" count={recipes.length}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {recipes.map((r) => (
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
  );
}

// A rank='genus' hub renders a DIFFERENT layout (aggregated across member species) —
// the species response shape (medicinal_uses/toxicities/harvests/…) is absent here, so
// the species view would crash on it. RFC-reference-granularity.
function GenusView({ plant }: { plant: PlantDetail }) {
  const members = plant.members || [];
  const uses = plant.uses || [];
  const compounds = (plant.compounds as unknown as GenusCompound[]) || [];
  const recipes = plant.recipes || [];
  const safety = plant.safety;
  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/plants" style={{ color: "var(--text-muted)", fontSize: 13 }}>Herbarium /</Link>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {plant.name}
            <span className="badge badge-blue">род</span>
          </h1>
          <div style={{ color: "var(--text-muted)", fontSize: 14, marginTop: 2 }}>
            {plant.name_latin && <em>{plant.name_latin}</em>} · {plant.member_count ?? members.length} видов
          </div>
        </div>
      </div>

      {plant.note && (
        <div className="card" style={{ marginBottom: 20, fontSize: 13, color: "var(--text-muted)", lineHeight: 1.5 }}>
          {plant.note}
        </div>
      )}

      {safety?.label && (
        <div className="card" style={{ marginBottom: 20 }}>
          <span style={{ color: "var(--text-muted)" }}>Безопасность (по роду):</span>{" "}
          <strong>{safety.label}</strong>
          {safety.note && <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>{safety.note}</div>}
          {!!safety.dangerous_members?.length && (
            <div style={{ fontSize: 13, marginTop: 6 }}>
              Опасные виды:{" "}
              {safety.dangerous_members.map((m) => (
                <Link key={m.id} href={`/plants/${m.id}`} className="badge badge-red" style={{ marginRight: 4, textDecoration: "none" }}>{m.name}</Link>
              ))}
            </div>
          )}
        </div>
      )}

      <Section title="Виды рода" count={members.length}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {members.map((m) => (
            <Link key={m.id} href={`/plants/${m.id}`} className="badge badge-green" style={{ padding: "6px 10px", textDecoration: "none" }}>
              {m.name}{m.name_latin && <em style={{ opacity: 0.7 }}> · {m.name_latin}</em>}
            </Link>
          ))}
        </div>
      </Section>

      {uses.length > 0 && (
        <Section title="Применения (по роду)" count={uses.length}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Действие</th><th>Видов</th><th>Показания</th><th>Источники</th></tr></thead>
              <tbody>
                {uses.map((u, i) => (
                  <tr key={i}>
                    <td>{u.action}</td>
                    <td>{u.n_species}</td>
                    <td>{u.indications?.join(", ") || "—"}</td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{u.sources?.join("; ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {compounds.length > 0 && (
        <Section title="Химический состав (по роду)" count={compounds.length}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {compounds.map((c, i) => (
              <span key={i} className="badge badge-green" style={{ padding: "6px 10px" }}
                title={`${c.n_species} видов: ${c.species?.join(", ") || ""}`}>
                {c.compound} <span style={{ opacity: 0.7 }}>×{c.n_species}</span>
              </span>
            ))}
          </div>
        </Section>
      )}

      {recipes.length > 0 && <RecipesSection recipes={recipes} />}
    </>
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
  if ((plant.rank || "species") === "genus") return <GenusView plant={plant} />;

  const medicinalUses = plant.medicinal_uses || [];
  const compounds = plant.compounds || [];
  const harvests = plant.harvests || [];
  const habitats = plant.habitats || [];
  const toxicities = plant.toxicities || [];
  const mentions = plant.mentions || [];
  const recipes = plant.recipes || [];
  const oils = plant.essential_oils || [];

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/plants" style={{ color: "var(--text-muted)", fontSize: 13 }}>Herbarium /</Link>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {plant.name}
            {plant.is_toxic && <span className="badge badge-red">⚠ toxic</span>}
          </h1>
          {plant.name_modern && plant.name_modern !== plant.name && (
            <div style={{ color: "var(--text-muted)", fontSize: 14, marginTop: 2 }}>
              совр. {plant.name_modern} · iNaturalist
            </div>
          )}
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
      {recipes.length > 0 && <RecipesSection recipes={recipes} />}

      {/* Cross-pillar link: essential oils derived from this plant */}
      {oils.length > 0 && (
        <Section title="Essential oils from this plant" count={oils.length}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {oils.map((o) => (
              <div key={o.id} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 14 }}>
                <Link href={`/oils/${o.id}`} style={{ fontWeight: 500 }}>{o.name}</Link>
                {o.name_latin && (
                  <span style={{ fontStyle: "italic", color: "var(--text-muted)", fontSize: 13 }}>{o.name_latin}</span>
                )}
                {o.part && <span className="badge badge-green">{o.part}</span>}
                {o.extraction && <span className="badge badge-green">{o.extraction}</span>}
                {o.uses_count > 0 && (
                  <span className="badge badge-blue" title="Aromatherapy use-facts">{o.uses_count}</span>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Toxicity — surfaced first when present */}
      {toxicities.length > 0 && (
        <Section title="⚠ Toxicity" count={toxicities.length}>
          {toxicities.map((t) => (
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
      <Section title="Medicinal uses" count={medicinalUses.length}>
        {medicinalUses.length === 0 ? (
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
                {medicinalUses.map((u) => (
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
      {compounds.length > 0 && (
        <Section title="Chemical composition" count={compounds.length}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {compounds.map((c) => {
              const title = [c.compound_group, c.part, c.notes].filter(Boolean).join(" · ");
              const inner = (
                <>
                  {c.compound}
                  {c.part && <span style={{ opacity: 0.7 }}> ({c.part})</span>}
                </>
              );
              return c.compound_id ? (
                <Link
                  key={c.id}
                  href={`/compounds/${c.compound_id}`}
                  className="badge badge-green"
                  style={{ padding: "6px 10px", textDecoration: "none" }}
                  title={title || "Open compound"}
                >
                  {inner}
                </Link>
              ) : (
                <div key={c.id} className="badge badge-green" style={{ padding: "6px 10px", opacity: 0.7 }} title={title || "Not yet linked to the compound vocabulary"}>
                  {inner}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Harvest */}
      {harvests.length > 0 && (
        <Section title="Collection & harvest" count={harvests.length}>
          {harvests.map((h) => (
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
      {habitats.length > 0 && (
        <Section title="Habitat & range" count={habitats.length}>
          {habitats.map((h) => (
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
      {mentions.length > 0 && (
        <Section title="Sources" count={mentions.length}>
          <div style={{ fontSize: 14 }}>
            {mentions.map((m) => (
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
