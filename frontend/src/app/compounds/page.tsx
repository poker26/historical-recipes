"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, Compound } from "@/lib/api";

export default function CompoundsPage() {
  const [compounds, setCompounds] = useState<Compound[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [compoundClass, setCompoundClass] = useState("");
  const [linkedOnly, setLinkedOnly] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.listCompounds().then(setCompounds).catch(console.error).finally(() => setLoading(false));
  }, []);

  const classes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of compounds) {
      if (c.compound_class) counts.set(c.compound_class, (counts.get(c.compound_class) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [compounds]);

  const byId = useMemo(() => {
    const m = new Map<string, Compound>();
    for (const c of compounds) m.set(c.id, c);
    return m;
  }, [compounds]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return compounds.filter((c) => {
      if (compoundClass && c.compound_class !== compoundClass) return false;
      if (linkedOnly && c.linked_facts === 0) return false;
      if (!q) return true;
      if (c.name.toLowerCase().includes(q)) return true;
      if (c.name_latin && c.name_latin.toLowerCase().includes(q)) return true;
      if (c.synonyms.some((s) => s.toLowerCase().includes(q))) return true;
      return false;
    });
  }, [compounds, search, compoundClass, linkedOnly]);

  const hasFilters = !!(search || compoundClass || linkedOnly);
  const totalFacts = compounds.reduce((sum, c) => sum + c.linked_facts, 0);

  return (
    <>
      <div className="page-header">
        <h1>Compounds</h1>
        <span style={{ color: "var(--text-muted)" }}>
          {compounds.length} terms · {totalFacts} plant-facts linked
        </span>
      </div>

      <div className="search-bar" style={{ flexWrap: "wrap", gap: 8 }}>
        <input placeholder="Search compounds, synonyms, Latin..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={compoundClass} onChange={(e) => setCompoundClass(e.target.value)} style={{ width: 240 }}>
          <option value="">Any class</option>
          {classes.map(([value, count]) => (
            <option key={value} value={value}>{value} ({count})</option>
          ))}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--text-muted)" }}>
          <input type="checkbox" checked={linkedOnly} onChange={(e) => setLinkedOnly(e.target.checked)} />
          Linked to plants only
        </label>
        {hasFilters && (
          <button
            className="btn btn-outline btn-sm"
            onClick={() => { setSearch(""); setCompoundClass(""); setLinkedOnly(false); }}
          >
            Clear
          </button>
        )}
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : compounds.length === 0 ? (
          <div className="empty">No compound vocabulary yet. Process a phytochemistry reference (domain=reference) first.</div>
        ) : filtered.length === 0 ? (
          <div className="empty">No compounds match the current filters.</div>
        ) : (
          <div className="card-grid">
            {filtered.map((c) => {
              const parent = c.parent_id ? byId.get(c.parent_id) : null;
              return (
                <Link href={`/compounds/${c.id}`} key={c.id} className="card" style={{ textDecoration: "none", color: "inherit" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                    <h3 style={{ margin: 0 }}>{c.name}</h3>
                    {c.linked_facts > 0 && (
                      <span className="badge badge-blue" title="Plant-facts normalized to this term">{c.linked_facts}</span>
                    )}
                  </div>
                  {c.name_latin && <div style={{ fontSize: 13, fontStyle: "italic", color: "var(--text-muted)" }}>{c.name_latin}</div>}
                  {c.compound_class && (
                    <div style={{ marginTop: 6 }}>
                      <span className="badge badge-green">{c.compound_class}</span>
                    </div>
                  )}
                  {parent && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
                      ⊂ {parent.name}
                    </div>
                  )}
                  {c.synonyms.length > 0 && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
                      {c.synonyms.join(", ")}
                    </div>
                  )}
                  {c.definition && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                      {c.definition}
                    </div>
                  )}
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
