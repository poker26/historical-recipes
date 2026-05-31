"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, Book, ActiveWorkflow, stepNamesForDomain } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

function StepProgress({ steps, completed, current }: { steps: string[]; completed: string[]; current: string | null }) {
  return (
    <div style={{ display: "flex", gap: 3, marginTop: 6 }}>
      {steps.map((step) => {
        const done = completed.includes(step);
        const active = step === current;
        const bg = done ? "var(--green)" : active ? "var(--blue)" : "var(--border)";
        return (
          <div
            key={step}
            title={step}
            style={{
              flex: 1,
              height: 6,
              borderRadius: 3,
              background: bg,
              opacity: active ? 1 : done ? 0.9 : 0.5,
              transition: "background 0.3s, opacity 0.3s",
            }}
          />
        );
      })}
    </div>
  );
}

export default function Dashboard() {
  const [books, setBooks] = useState<Book[]>([]);
  const [active, setActive] = useState<ActiveWorkflow[]>([]);
  const [loading, setLoading] = useState(true);
  const prevActiveIds = useRef<Set<string>>(new Set());

  const fetchBooks = () =>
    api.listBooks().then(setBooks).catch(console.error);

  useEffect(() => {
    fetchBooks().finally(() => setLoading(false));

    const poll = () => {
      api.activeWorkflows()
        .then((list) => {
          setActive(list);
          // If a workflow finished (id present last tick, gone now), refresh
          // the books table so its status badge updates immediately.
          const ids = new Set(list.map((w) => w.book_id));
          let finished = false;
          prevActiveIds.current.forEach((id) => {
            if (!ids.has(id)) finished = true;
          });
          if (finished) fetchBooks();
          prevActiveIds.current = ids;
        })
        .catch(console.error);
    };

    poll();
    const interval = setInterval(poll, 3000);
    return () => clearInterval(interval);
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

      {active.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <span className="spinner" />
            <h2 style={{ margin: 0 }}>Processing now</h2>
            <span className="badge badge-blue">{active.length}</span>
          </div>
          {active.map((w, i) => {
            const completed = w.completed_steps || [];
            const done = completed.length;
            const steps = stepNamesForDomain(w.domain);
            const retrying = (w.current_attempt ?? 1) > 1;
            return (
              <div
                key={w.book_id}
                style={{
                  padding: "12px 0",
                  borderTop: i === 0 ? "none" : "1px solid var(--border)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                  <Link href={`/books/${w.book_id}/wizard`} style={{ fontWeight: 600 }}>
                    {w.title || w.book_id}
                  </Link>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {retrying && <span className="badge badge-yellow">retry {w.current_attempt}</span>}
                    <span className="badge badge-blue">{w.current_step || w.status}</span>
                    <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                      {done}/{steps.length}
                    </span>
                  </div>
                </div>
                <StepProgress steps={steps} completed={completed} current={w.current_step ?? null} />
                {w.current_detail && (
                  <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 6 }}>
                    {w.current_detail}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

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
