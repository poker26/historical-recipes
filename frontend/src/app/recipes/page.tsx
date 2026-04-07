"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Recipe } from "@/lib/api";

const CATEGORIES = ["", "водка", "ликёр", "настойка", "бальзам", "масло", "вода", "эссенция", "тинктура", "ратафия", "розолия"];

export default function RecipesPage() {
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");

  useEffect(() => {
    api.listRecipes({ category: category || undefined, q: search || undefined })
      .then(setRecipes).catch(console.error).finally(() => setLoading(false));
  }, [category, search]);

  return (
    <>
      <div className="page-header">
        <h1>Recipes</h1>
        <span style={{ color: "var(--text-muted)" }}>{recipes.length} total</span>
      </div>

      <div className="search-bar">
        <input
          placeholder="Search recipes..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={category} onChange={(e) => setCategory(e.target.value)} style={{ width: 160 }}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c || "All categories"}</option>
          ))}
        </select>
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : recipes.length === 0 ? (
          <div className="empty">No recipes found. Process and parse books first.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Name</th><th>Category</th><th>Year</th><th>Indexed</th></tr>
              </thead>
              <tbody>
                {recipes.map((r) => (
                  <tr key={r.id}>
                    <td><Link href={`/recipes/${r.id}`}>{r.name}</Link></td>
                    <td>{r.category ? <span className="badge badge-blue">{r.category}</span> : "—"}</td>
                    <td>{r.year || "—"}</td>
                    <td>{r.indexed_at ? <span className="badge badge-green">indexed</span> : <span className="badge badge-gray">no</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
