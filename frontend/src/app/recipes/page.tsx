"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, Recipe } from "@/lib/api";
import Pagination from "@/components/Pagination";

const CATEGORIES = ["", "водка", "ликёр", "настойка", "бальзам", "масло", "вода", "эссенция", "тинктура", "ратафия", "розолия", "отвар", "настой", "чай", "сбор", "мазь", "сироп", "порошок", "припарка", "капли", "примочка", "другое"];
const PAGE_SIZE = 50;

// Read/write list state (search, filters, page) in the URL query string so that
// opening a card and pressing Back restores the exact list view — same page and
// filters — instead of resetting to the first page. Uses window.history directly
// (no useSearchParams → no Suspense boundary requirement at build time).
function readParam(key: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(key) || "";
}
function readPageParam(): number {
  const v = parseInt(readParam("page"), 10);
  return Number.isNaN(v) || v < 0 ? 0 : v;
}

export default function RecipesPage() {
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(() => readParam("q"));
  const [category, setCategory] = useState(() => readParam("category"));
  const [page, setPage] = useState(readPageParam);

  // New search/filter → jump back to the first page. Skipped on the initial
  // mount so a page restored from the URL (Back from a card) isn't clobbered.
  const didMount = useRef(false);
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return; }
    setPage(0);
  }, [category, search]);

  // Reflect the current view in the URL (replaceState = no new history entry per
  // click), so navigating into a card and pressing Back returns to this exact page.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (category) params.set("category", category);
    if (page > 0) params.set("page", String(page));
    const qs = params.toString();
    const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(window.history.state, "", url);
  }, [search, category, page]);

  useEffect(() => {
    setLoading(true);
    api.listRecipes({
      category: category || undefined,
      q: search || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then(({ items, total }) => { setRecipes(items); setTotal(total); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [category, search, page]);

  return (
    <>
      <div className="page-header">
        <h1>Recipes</h1>
        <span style={{ color: "var(--text-muted)" }}>{total} total</span>
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
                <tr><th>Name</th><th>Book</th><th>Category</th><th>Year</th><th>Indexed</th></tr>
              </thead>
              <tbody>
                {recipes.map((r) => (
                  <tr key={r.id}>
                    <td><Link href={`/recipes/${r.id}`}>{r.name}</Link></td>
                    <td>
                      {r.book_title ? (
                        <Link href={`/books/${r.book_id}`} style={{ color: "var(--text-muted)" }}>
                          {r.book_title}
                        </Link>
                      ) : "—"}
                    </td>
                    <td>{r.category ? <span className="badge badge-blue">{r.category}</span> : "—"}</td>
                    <td>{r.year || r.book_year || "—"}</td>
                    <td>{r.indexed_at ? <span className="badge badge-green">indexed</span> : <span className="badge badge-gray">no</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {!loading && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      )}
    </>
  );
}
