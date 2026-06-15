"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, OilDetail } from "@/lib/api";

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

export default function OilDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [o, setO] = useState<OilDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getOil(id).then(setO).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!o) return <div className="empty">Essential oil not found</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/oils" style={{ color: "var(--text-muted)", fontSize: 13 }}>Essential Oils /</Link>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {o.name}
            {o.name_latin && <em style={{ fontSize: 16, color: "var(--text-muted)" }}>{o.name_latin}</em>}
          </h1>
        </div>
      </div>

      {/* Identity */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, fontSize: 14 }}>
          <div>
            <span style={{ color: "var(--text-muted)" }}>Source plant:</span>{" "}
            {o.plant ? (
              <Link href={`/plants/${o.plant.id}`}>{o.plant.name}</Link>
            ) : (
              o.source_plant_raw || "—"
            )}
            {o.plant?.name_latin && <em style={{ color: "var(--text-muted)" }}> ({o.plant.name_latin})</em>}
          </div>
          <div><span style={{ color: "var(--text-muted)" }}>Part:</span> {o.part || "—"}</div>
          <div><span style={{ color: "var(--text-muted)" }}>Extraction:</span> {o.extraction || "—"}</div>
          <div><span style={{ color: "var(--text-muted)" }}>Synonyms:</span> {o.synonyms.length ? o.synonyms.join(", ") : "—"}</div>
        </div>
        {o.aroma_profile && (
          <div style={{ marginTop: 14, fontSize: 14 }}>
            <span style={{ color: "var(--text-muted)" }}>Aroma:</span> {o.aroma_profile}
          </div>
        )}
        {o.description && (
          <div style={{ marginTop: 12, fontSize: 14, lineHeight: 1.55 }}>{o.description}</div>
        )}
        {o.original_text && (
          <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic", marginTop: 10, borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
            {o.original_text}
          </div>
        )}
      </div>

      {/* Aromatherapy uses */}
      <Section title="Aromatherapy uses" count={o.uses.length}>
        {o.uses.length === 0 ? (
          <div className="empty">No grounded use-facts extracted for this oil.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {o.uses.map((u) => (
              <div key={u.id} style={{ padding: "10px 0", borderTop: "1px solid var(--border)" }}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                  {u.action && <span className="badge badge-green">{u.action}</span>}
                  {u.application && <span className="badge badge-blue">{u.application}</span>}
                  {u.indication_concepts.map((c) => (
                    <span key={c} className="badge" style={{ background: "var(--border)" }}>{c}</span>
                  ))}
                </div>
                {u.indications && (
                  <div style={{ fontSize: 14, marginTop: 6 }}>
                    <span style={{ color: "var(--text-muted)" }}>For:</span> {u.indications}
                  </div>
                )}
                {u.dosage && (
                  <div style={{ fontSize: 13, marginTop: 4 }}>
                    <span style={{ color: "var(--text-muted)" }}>Dosage:</span> {u.dosage}
                  </div>
                )}
                {u.contraindications && (
                  <div style={{ fontSize: 13, marginTop: 4, color: "var(--danger, #c0392b)" }}>
                    <span style={{ color: "var(--text-muted)" }}>Caution:</span> {u.contraindications}
                  </div>
                )}
                {u.original_text && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic", marginTop: 6, borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
                    {u.original_text}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
