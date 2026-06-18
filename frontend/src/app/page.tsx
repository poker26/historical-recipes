"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, Book, ActiveWorkflow, OpsWorkflow, stepNamesForDomain } from "@/lib/api";
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
  const [ops, setOps] = useState<OpsWorkflow[]>([]);
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

    // Long cleanup/dispatcher workflows (fill-latin, plant-cleanup, quest-sets…) —
    // polled less often; surfaces RUNNING + recently-FAILED so a silent overnight
    // failure is visible.
    const pollOps = () =>
      api.opsWorkflows().then((r) => setOps(r.workflows)).catch(console.error);
    pollOps();
    const opsInterval = setInterval(pollOps, 10000);

    return () => { clearInterval(interval); clearInterval(opsInterval); };
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

      {ops.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>Фоновые задачи (cleanup)</h2>
            {ops.some((w) => w.status === "FAILED") && (
              <span className="badge badge-red">{ops.filter((w) => w.status === "FAILED").length} FAILED</span>
            )}
          </div>
          {ops.map((w, i) => {
            const badge =
              w.status === "RUNNING" ? "badge-blue" :
              w.status === "FAILED" ? "badge-red" :
              w.status === "COMPLETED" ? "badge-green" : "badge-gray";
            const start = w.start_time ? new Date(w.start_time) : null;
            const end = w.close_time ? new Date(w.close_time) : new Date();
            const dur = start ? Math.round((end.getTime() - start.getTime()) / 60000) : null;
            const prog = w.progress
              ? Object.entries(w.progress).map(([k, v]) => `${k}=${v}`).join("  ")
              : null;
            return (
              <div key={w.id + i} style={{ padding: "10px 0", borderTop: i === 0 ? "none" : "1px solid var(--border)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                  <span style={{ fontWeight: 600 }}>{w.type.replace("Workflow", "")}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {(w.attempt ?? 1) > 1 && <span className="badge badge-yellow">retry {w.attempt}</span>}
                    <span className={`badge ${badge}`}>{w.status}</span>
                    {dur != null && <span style={{ color: "var(--text-muted)", fontSize: 12 }}>{dur}m</span>}
                  </div>
                </div>
                {prog && <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 5 }}>{prog}</div>}
                {w.status === "FAILED" && w.error && (
                  <div style={{ color: "var(--red, #c0392b)", fontSize: 12, marginTop: 5, fontFamily: "monospace" }}>
                    ⚠ {w.error}
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
