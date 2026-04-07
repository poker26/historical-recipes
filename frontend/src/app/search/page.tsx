"use client";

import { useState } from "react";
import { api, SearchResult, SearchResponse } from "@/lib/api";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [collection, setCollection] = useState("");
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    try {
      const res = await api.search(query, mode, collection || undefined);
      setResults(res);
    } catch (err) {
      alert(`Search error: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>Search Test Bench</h1>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <form onSubmit={handleSearch}>
          <div className="search-bar" style={{ marginBottom: 12 }}>
            <input
              placeholder="Enter search query... e.g. анисовая водка"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ fontSize: 16 }}
            />
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="spinner" /> : "Search"}
            </button>
          </div>
          <div style={{ display: "flex", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Mode</label>
              <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ width: 140 }}>
                <option value="hybrid">Hybrid (RRF)</option>
                <option value="dense">Dense only</option>
                <option value="sparse">Sparse only</option>
              </select>
            </div>
            <div>
              <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Collection</label>
              <select value={collection} onChange={(e) => setCollection(e.target.value)} style={{ width: 160 }}>
                <option value="">Both collections</option>
                <option value="recipes">Recipes</option>
                <option value="herbalism">Herbalism</option>
              </select>
            </div>
          </div>
        </form>
      </div>

      {results && (
        <>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>
            {results.results.length} results for &quot;{results.query}&quot; | mode: {results.mode} | collections: {results.collections_searched.join(", ")}
          </div>

          {results.results.length === 0 ? (
            <div className="card empty">No results found</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {results.results.map((r, i) => (
                <ResultCard key={r.id} result={r} rank={i + 1} />
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function ResultCard({ result, rank }: { result: SearchResult; rank: number }) {
  const [expanded, setExpanded] = useState(false);
  const payload = result.payload;

  return (
    <div className="card" style={{ cursor: "pointer" }} onClick={() => setExpanded(!expanded)}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ fontSize: 20, fontWeight: 700, color: "var(--text-muted)" }}>#{rank}</span>
          <div>
            <div style={{ fontWeight: 600, fontSize: 16 }}>
              {String(payload.recipe_name || payload.name || "—")}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", display: "flex", gap: 8 }}>
              <span className={`badge ${result.collection === "recipes" ? "badge-blue" : "badge-green"}`}>
                {result.collection}
              </span>
              {payload.category ? <span className="badge badge-gray">{String(payload.category)}</span> : null}
              {payload.year ? <span>Year: {String(payload.year)}</span> : null}
              {payload.book_title ? <span>Book: {String(payload.book_title)}</span> : null}
            </div>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 20, fontWeight: 700, color: result.score > 0.7 ? "var(--green)" : result.score > 0.4 ? "var(--yellow)" : "var(--text-muted)" }}>
            {result.score.toFixed(3)}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>score</div>
        </div>
      </div>
      {expanded && payload.content ? (
        <pre style={{ marginTop: 12, fontSize: 13, whiteSpace: "pre-wrap", background: "var(--bg)", padding: 12, borderRadius: 6, maxHeight: 300, overflow: "auto" }}>
          {String(payload.content)}
        </pre>
      )}
    </div>
  );
}
