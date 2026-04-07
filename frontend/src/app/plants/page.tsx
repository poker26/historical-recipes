"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Plant } from "@/lib/api";

export default function PlantsPage() {
  const [plants, setPlants] = useState<Plant[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api.listPlants(search || undefined)
      .then(setPlants).catch(console.error).finally(() => setLoading(false));
  }, [search]);

  return (
    <>
      <div className="page-header">
        <h1>Herbarium</h1>
        <span style={{ color: "var(--text-muted)" }}>{plants.length} plants</span>
      </div>

      <div className="search-bar">
        <input placeholder="Search plants..." value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : plants.length === 0 ? (
          <div className="empty">No plants in the herbarium yet. Process herbalism books first.</div>
        ) : (
          <div className="card-grid">
            {plants.map((p) => (
              <Link href={`/plants/${p.id}`} key={p.id} className="card" style={{ textDecoration: "none", color: "inherit" }}>
                <h3>{p.name}</h3>
                {p.name_latin && <div style={{ fontSize: 13, fontStyle: "italic", color: "var(--text-muted)" }}>{p.name_latin}</div>}
                {p.family && <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>{p.family}</div>}
                {p.parts_used && p.parts_used.length > 0 && (
                  <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {p.parts_used.map((part) => (
                      <span key={part} className="badge badge-green">{part}</span>
                    ))}
                  </div>
                )}
                {p.names_historical && p.names_historical.length > 0 && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
                    Historical: {p.names_historical.join(", ")}
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
