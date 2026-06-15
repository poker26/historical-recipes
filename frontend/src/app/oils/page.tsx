"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, Oil } from "@/lib/api";

export default function OilsPage() {
  const [oils, setOils] = useState<Oil[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [usesOnly, setUsesOnly] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .listOils({ limit: 500 })
      .then((r) => {
        setOils(r.items);
        setTotal(r.total);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return oils.filter((o) => {
      if (usesOnly && o.uses_count === 0) return false;
      if (!q) return true;
      if (o.name.toLowerCase().includes(q)) return true;
      if (o.name_latin && o.name_latin.toLowerCase().includes(q)) return true;
      if (o.plant_name && o.plant_name.toLowerCase().includes(q)) return true;
      return false;
    });
  }, [oils, search, usesOnly]);

  const hasFilters = !!(search || usesOnly);
  const totalUses = oils.reduce((sum, o) => sum + o.uses_count, 0);

  return (
    <>
      <div className="page-header">
        <h1>Essential Oils</h1>
        <span style={{ color: "var(--text-muted)" }}>
          {total} oils · {totalUses} aromatherapy use-facts
        </span>
      </div>

      <div className="search-bar" style={{ flexWrap: "wrap", gap: 8 }}>
        <input
          placeholder="Search oils, Latin, source plant..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--text-muted)" }}>
          <input type="checkbox" checked={usesOnly} onChange={(e) => setUsesOnly(e.target.checked)} />
          With use-facts only
        </label>
        {hasFilters && (
          <button
            className="btn btn-outline btn-sm"
            onClick={() => {
              setSearch("");
              setUsesOnly(false);
            }}
          >
            Clear
          </button>
        )}
      </div>

      <div className="card">
        {loading ? (
          <div className="empty">
            <span className="spinner" />
          </div>
        ) : oils.length === 0 ? (
          <div className="empty">No essential oils yet. Process an aromatherapy reference (domain=aromatherapy) first.</div>
        ) : filtered.length === 0 ? (
          <div className="empty">No oils match the current filters.</div>
        ) : (
          <div className="card-grid">
            {filtered.map((o) => (
              <Link href={`/oils/${o.id}`} key={o.id} className="card" style={{ textDecoration: "none", color: "inherit" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                  <h3 style={{ margin: 0 }}>{o.name}</h3>
                  {o.uses_count > 0 && (
                    <span className="badge badge-blue" title="Aromatherapy use-facts">{o.uses_count}</span>
                  )}
                </div>
                {o.name_latin && (
                  <div style={{ fontSize: 13, fontStyle: "italic", color: "var(--text-muted)" }}>{o.name_latin}</div>
                )}
                {o.plant_name && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
                    ← {o.plant_name}
                    {o.plant_name_latin && <em> ({o.plant_name_latin})</em>}
                  </div>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {o.part && <span className="badge badge-green">{o.part}</span>}
                  {o.extraction && <span className="badge badge-green">{o.extraction}</span>}
                </div>
                {o.aroma_profile && (
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--text-muted)",
                      marginTop: 6,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                    }}
                  >
                    {o.aroma_profile}
                  </div>
                )}
              </Link>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
