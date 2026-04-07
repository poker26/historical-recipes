"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Book } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

export default function Dashboard() {
  const [books, setBooks] = useState<Book[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listBooks().then(setBooks).catch(console.error).finally(() => setLoading(false));
  }, []);

  const stats = {
    total: books.length,
    recipes_domain: books.filter((b) => b.domain === "recipes").length,
    herbalism_domain: books.filter((b) => b.domain === "herbalism").length,
    indexed: books.filter((b) => b.status === "indexed" || b.status === "verified").length,
    processing: books.filter((b) =>
      ["preprocessing", "ocr_pending", "ocr_done", "postprocessing", "normalized", "parsed", "processing"].includes(b.status)
    ).length,
    uploaded: books.filter((b) => b.status === "uploaded").length,
  };

  if (loading) return <div className="empty"><span className="spinner" /></div>;

  return (
    <>
      <div className="page-header">
        <h1>Dashboard</h1>
        <Link href="/books" className="btn btn-primary">+ Upload Book</Link>
      </div>

      <div className="stat-grid">
        <div className="stat-card">
          <div className="label">Books Total</div>
          <div className="value">{stats.total}</div>
        </div>
        <div className="stat-card">
          <div className="label">Recipes Domain</div>
          <div className="value">{stats.recipes_domain}</div>
        </div>
        <div className="stat-card">
          <div className="label">Herbalism Domain</div>
          <div className="value">{stats.herbalism_domain}</div>
        </div>
        <div className="stat-card">
          <div className="label">Indexed</div>
          <div className="value" style={{ color: "var(--green)" }}>{stats.indexed}</div>
        </div>
        <div className="stat-card">
          <div className="label">Processing</div>
          <div className="value" style={{ color: "var(--blue)" }}>{stats.processing}</div>
        </div>
        <div className="stat-card">
          <div className="label">Awaiting</div>
          <div className="value" style={{ color: "var(--text-muted)" }}>{stats.uploaded}</div>
        </div>
      </div>

      <div className="card">
        <h2 style={{ marginBottom: 16 }}>Recent Books</h2>
        {books.length === 0 ? (
          <div className="empty">No books yet. Upload your first book.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Domain</th>
                  <th>Year</th>
                  <th>Status</th>
                  <th>Added</th>
                </tr>
              </thead>
              <tbody>
                {books.slice(0, 10).map((book) => (
                  <tr key={book.id}>
                    <td><Link href={`/books/${book.id}`}>{book.title}</Link></td>
                    <td><span className={`badge ${book.domain === "recipes" ? "badge-blue" : "badge-green"}`}>{book.domain}</span></td>
                    <td>{book.year || "—"}</td>
                    <td><StatusBadge status={book.status} /></td>
                    <td style={{ color: "var(--text-muted)" }}>{new Date(book.created_at).toLocaleDateString("ru")}</td>
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
