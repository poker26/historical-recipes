"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Book } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

// Columns the table can be sorted by, plus how to read the value off a Book.
type SortKey = "title" | "author" | "year" | "domain" | "pdf_type" | "language" | "status" | "created_at";
const SORT_GETTERS: Record<SortKey, (b: Book) => string | number | null> = {
  title: (b) => b.title,
  author: (b) => b.author,
  year: (b) => b.year,
  domain: (b) => b.domain,
  pdf_type: (b) => b.pdf_type,
  language: (b) => b.language,
  status: (b) => b.status,
  created_at: (b) => b.created_at,
};

export default function BooksPage() {
  const [books, setBooks] = useState<Book[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterDomain, setFilterDomain] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);

  const fetchBooks = () => {
    setLoading(true);
    api.listBooks({
      domain: filterDomain || undefined,
      status: filterStatus || undefined,
    }).then(setBooks).catch(console.error).finally(() => setLoading(false));
  };

  useEffect(fetchBooks, [filterDomain, filterStatus]);

  // Click a column header: same column flips direction, a new one starts asc
  // (created_at starts desc — newest first is the natural default for a date).
  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "created_at" || key === "year" ? "desc" : "asc");
    }
  };

  // Search (title + author) and sort happen client-side over the already-fetched
  // list — instant, no extra round-trips. Domain/status stay server-side filters.
  const q = search.trim().toLowerCase();
  const displayed = books
    .filter(
      (b) =>
        !q ||
        b.title.toLowerCase().includes(q) ||
        (b.author || "").toLowerCase().includes(q),
    )
    .sort((a, b) => {
      const av = SORT_GETTERS[sortKey](a);
      const bv = SORT_GETTERS[sortKey](b);
      // Nulls/empties always sort last, regardless of direction.
      const aEmpty = av === null || av === "";
      const bEmpty = bv === null || bv === "";
      if (aEmpty && bEmpty) return 0;
      if (aEmpty) return 1;
      if (bEmpty) return -1;
      let cmp: number;
      if (typeof av === "number" && typeof bv === "number") {
        cmp = av - bv;
      } else {
        cmp = String(av).localeCompare(String(bv), "ru");
      }
      return sortDir === "asc" ? cmp : -cmp;
    });

  const sortArrow = (key: SortKey) =>
    key === sortKey ? (sortDir === "asc" ? " ▲" : " ▼") : "";

  const handleDelete = async (book: Book) => {
    if (!confirm(`Delete "${book.title}"? This removes the book, its files and all indexed vectors. This cannot be undone.`)) {
      return;
    }
    try {
      await api.deleteBook(book.id);
      fetchBooks();
    } catch (err) {
      alert(`Delete failed: ${err}`);
    }
  };

  const handleUpload = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (uploading) return;
    const form = e.currentTarget;
    const formData = new FormData(form);
    setUploading(true);
    try {
      await api.uploadBook(formData);
      form.reset();
      setShowUpload(false);
      fetchBooks();
    } catch (err) {
      alert(`Upload failed: ${err}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>Library {!loading && <span style={{ color: "var(--text-muted)", fontWeight: 400, fontSize: 16 }}>({displayed.length}{displayed.length !== books.length ? ` / ${books.length}` : ""})</span>}</h1>
        <button className="btn btn-primary" onClick={() => setShowUpload(!showUpload)}>
          + Upload Book
        </button>
      </div>

      {showUpload && (
        <div className="card" style={{ marginBottom: 20 }}>
          <h2 style={{ marginBottom: 16 }}>Upload New Book</h2>
          <form onSubmit={handleUpload}>
            <div className="form-group">
              <label>File (PDF, DjVu, TXT, DOCX)</label>
              <input type="file" name="file" accept=".pdf,.djvu,.txt,.docx" required />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label>Title</label>
                <input name="title" required placeholder="Book title" />
              </div>
              <div className="form-group">
                <label>Domain</label>
                <select name="domain">
                  <option value="recipes">Recipes</option>
                  <option value="herbalism">Herbalism</option>
                  <option value="fungi">Fungi (mushroom guide)</option>
                  <option value="reference">Reference (compound/property dictionary)</option>
                </select>
              </div>
              <div className="form-group">
                <label>Author</label>
                <input name="author" placeholder="Optional" />
              </div>
              <div className="form-group">
                <label>Year</label>
                <input name="year" type="number" placeholder="e.g. 1871" />
              </div>
              <div className="form-group">
                <label>Language</label>
                <select name="language">
                  <option value="pre_reform_ru">Pre-reform Russian</option>
                  <option value="modern_ru">Modern Russian</option>
                </select>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button type="submit" className="btn btn-primary" disabled={uploading}>
                {uploading ? "Uploading…" : "Upload"}
              </button>
              <button type="button" className="btn btn-outline" onClick={() => setShowUpload(false)} disabled={uploading}>Cancel</button>
              {uploading && <span className="spinner" />}
            </div>
          </form>
        </div>
      )}

      <div className="search-bar">
        <input
          type="search"
          placeholder="Search by title or author…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={filterDomain} onChange={(e) => setFilterDomain(e.target.value)} style={{ width: 160 }}>
          <option value="">All domains</option>
          <option value="recipes">Recipes</option>
          <option value="herbalism">Herbalism</option>
          <option value="fungi">Fungi</option>
          <option value="reference">Reference</option>
        </select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} style={{ width: 160 }}>
          <option value="">All statuses</option>
          <option value="uploaded">Uploaded</option>
          <option value="processing">Processing</option>
          <option value="indexed">Indexed</option>
          <option value="verified">Verified</option>
        </select>
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : displayed.length === 0 ? (
          <div className="empty">{books.length === 0 ? "No books found" : "No books match your search"}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {([
                    ["title", "Title"],
                    ["author", "Author"],
                    ["year", "Year"],
                    ["domain", "Domain"],
                    ["pdf_type", "PDF Type"],
                    ["language", "Language"],
                    ["status", "Status"],
                  ] as [SortKey, string][]).map(([key, label]) => (
                    <th
                      key={key}
                      onClick={() => toggleSort(key)}
                      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
                      title="Click to sort"
                    >
                      {label}{sortArrow(key)}
                    </th>
                  ))}
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {displayed.map((book) => (
                  <tr key={book.id}>
                    <td><Link href={`/books/${book.id}`}>{book.title}</Link></td>
                    <td>{book.author || "—"}</td>
                    <td>{book.year || "—"}</td>
                    <td><span className={`badge ${({ recipes: "badge-blue", herbalism: "badge-green", fungi: "badge-yellow", reference: "badge-gray" } as Record<string, string>)[book.domain] || "badge-gray"}`}>{book.domain}</span></td>
                    <td><span className="badge badge-gray">{book.pdf_type}</span></td>
                    <td>{book.language === "pre_reform_ru" ? "Pre-reform" : "Modern"}</td>
                    <td><StatusBadge status={book.status} /></td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleDelete(book)}
                        title="Delete book"
                      >
                        Delete
                      </button>
                    </td>
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
