"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, CompoundDetail } from "@/lib/api";

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

export default function CompoundDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [c, setC] = useState<CompoundDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getCompound(id).then(setC).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!c) return <div className="empty">Compound not found</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/compounds" style={{ color: "var(--text-muted)", fontSize: 13 }}>Compounds /</Link>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {c.name}
            {c.compound_class && <span className="badge badge-green">{c.compound_class}</span>}
          </h1>
        </div>
      </div>

      {/* Identity */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, fontSize: 14 }}>
          <div><span style={{ color: "var(--text-muted)" }}>Latin:</span> <em>{c.name_latin || "—"}</em></div>
          <div>
            <span style={{ color: "var(--text-muted)" }}>Class:</span>{" "}
            {c.compound_class || "—"}
          </div>
          <div>
            <span style={{ color: "var(--text-muted)" }}>Parent:</span>{" "}
            {c.parent ? <Link href={`/compounds/${c.parent.id}`}>{c.parent.name}</Link> : "—"}
          </div>
          <div>
            <span style={{ color: "var(--text-muted)" }}>Synonyms:</span>{" "}
            {c.synonyms.length ? c.synonyms.join(", ") : "—"}
          </div>
        </div>
        {c.definition && (
          <div style={{ marginTop: 14, fontSize: 14, lineHeight: 1.55 }}>{c.definition}</div>
        )}
        {c.original_text && (
          <div style={{ fontSize: 12, color: "var(--text-muted)", fontStyle: "italic", marginTop: 10, borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
            {c.original_text}
          </div>
        )}
      </div>

      {/* Sub-classes / children */}
      {c.children.length > 0 && (
        <Section title="Sub-classes" count={c.children.length}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {c.children.map((ch) => (
              <Link href={`/compounds/${ch.id}`} key={ch.id} className="badge badge-green" style={{ padding: "6px 10px", textDecoration: "none" }}>
                {ch.name}
              </Link>
            ))}
          </div>
        </Section>
      )}

      {/* Plants containing this compound (reverse link) */}
      <Section title="Plants containing this compound" count={c.plants.length}>
        {c.plants.length === 0 ? (
          <div className="empty">No plant facts normalize to this term yet. Run normalize after the next phytochemistry book.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {c.plants.map((p) => (
              <div key={p.id} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 14, padding: "6px 0", borderTop: "1px solid var(--border)" }}>
                <Link href={`/plants/${p.id}`} style={{ fontWeight: 500 }}>{p.name}</Link>
                {p.name_latin && <span style={{ fontStyle: "italic", color: "var(--text-muted)", fontSize: 13 }}>{p.name_latin}</span>}
                {p.parts.map((part) => (
                  <span key={part} className="badge badge-green">{part}</span>
                ))}
                {p.raw_names.length > 0 && (
                  <span style={{ color: "var(--text-muted)", fontSize: 12 }} title="As written in the source">
                    ({p.raw_names.join(", ")})
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
