"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, BookDetail, BookChunk, BookPage as PageType } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

type Tab = "overview" | "pages" | "chunks" | "logs";

export default function BookDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [book, setBook] = useState<BookDetail | null>(null);
  const [pages, setPages] = useState<PageType[]>([]);
  const [chunks, setChunks] = useState<BookChunk[]>([]);
  const [tab, setTab] = useState<Tab>("overview");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getBook(id).then(setBook).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (tab === "pages") api.getPages(id).then(setPages).catch(console.error);
    if (tab === "chunks") api.getChunks(id).then(setChunks).catch(console.error);
  }, [tab, id]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!book) return <div className="empty">Book not found</div>;

  const handleProcess = async () => {
    await api.processBook(id);
    const updated = await api.getBook(id);
    setBook(updated);
  };

  const handleIndex = async () => {
    await api.indexBook(id);
    const updated = await api.getBook(id);
    setBook(updated);
  };

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/books" style={{ color: "var(--text-muted)", fontSize: 13 }}>Books /</Link>
          <h1>{book.title}</h1>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" onClick={handleProcess}>Process</button>
          <button className="btn btn-outline" onClick={handleIndex}>Index</button>
        </div>
      </div>

      <div className="stat-grid">
        <div className="stat-card">
          <div className="label">Status</div>
          <div style={{ marginTop: 8 }}><StatusBadge status={book.status} /></div>
        </div>
        <div className="stat-card">
          <div className="label">Pages</div>
          <div className="value">{book.pages_count}</div>
        </div>
        <div className="stat-card">
          <div className="label">Chunks</div>
          <div className="value">{book.chunks_count}</div>
        </div>
        <div className="stat-card">
          <div className="label">Recipes</div>
          <div className="value">{book.recipes_count}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, fontSize: 14 }}>
          <div><span style={{ color: "var(--text-muted)" }}>Author:</span> {book.author || "—"}</div>
          <div><span style={{ color: "var(--text-muted)" }}>Year:</span> {book.year || "—"}</div>
          <div><span style={{ color: "var(--text-muted)" }}>Domain:</span> <span className={`badge ${book.domain === "recipes" ? "badge-blue" : "badge-green"}`}>{book.domain}</span></div>
          <div><span style={{ color: "var(--text-muted)" }}>PDF Type:</span> {book.pdf_type}</div>
          <div><span style={{ color: "var(--text-muted)" }}>Language:</span> {book.language}</div>
          <div><span style={{ color: "var(--text-muted)" }}>Added:</span> {new Date(book.created_at).toLocaleString("ru")}</div>
        </div>
      </div>

      <div className="tabs">
        {(["overview", "pages", "chunks", "logs"] as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "overview" ? "Overview" : t === "pages" ? `Pages (${book.pages_count})` : t === "chunks" ? `Chunks (${book.chunks_count})` : "Processing Log"}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="card">
          <p style={{ color: "var(--text-muted)" }}>
            Select Pages or Chunks tab to view OCR results, or check Processing Log for history.
          </p>
        </div>
      )}

      {tab === "pages" && (
        <div className="card">
          {pages.length === 0 ? (
            <div className="empty">No pages yet. Run processing first.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>#</th><th>Status</th><th>OCR Confidence</th><th>Review</th><th>Text Preview</th></tr>
                </thead>
                <tbody>
                  {pages.map((p) => (
                    <tr key={p.id}>
                      <td>{p.page_number}</td>
                      <td><StatusBadge status={p.status} /></td>
                      <td>
                        {p.ocr_confidence != null ? (
                          <span style={{ color: p.ocr_confidence >= 80 ? "var(--green)" : p.ocr_confidence >= 60 ? "var(--yellow)" : "var(--red)" }}>
                            {p.ocr_confidence.toFixed(1)}%
                          </span>
                        ) : "—"}
                      </td>
                      <td>{p.needs_review ? <span className="badge badge-yellow">needs review</span> : "OK"}</td>
                      <td style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-muted)" }}>
                        {p.raw_text?.slice(0, 100) || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "chunks" && (
        <div className="card">
          {chunks.length === 0 ? (
            <div className="empty">No chunks yet. Run processing first.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {chunks.map((c) => (
                <div key={c.id} className="card" style={{ padding: 16 }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                    <span style={{ color: "var(--text-muted)", fontSize: 12 }}>#{c.chunk_index}</span>
                    <StatusBadge status={c.status} />
                    {c.chunk_type && <span className="badge badge-blue">{c.chunk_type}</span>}
                    {c.layout_zone && <span className="badge badge-gray">{c.layout_zone}</span>}
                    {c.page_start && <span style={{ color: "var(--text-muted)", fontSize: 12 }}>pp. {c.page_start}–{c.page_end || c.page_start}</span>}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>RAW</div>
                      <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto", background: "var(--bg)", padding: 8, borderRadius: 6 }}>
                        {c.raw_text || "—"}
                      </pre>
                    </div>
                    <div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>NORMALIZED</div>
                      <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto", background: "var(--bg)", padding: 8, borderRadius: 6 }}>
                        {c.normalized_text || c.cleaned_text || "—"}
                      </pre>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "logs" && (
        <div className="card">
          {book.logs.length === 0 ? (
            <div className="empty">No processing logs yet.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Step</th><th>Status</th><th>Details</th><th>Time</th></tr>
                </thead>
                <tbody>
                  {book.logs.map((log) => (
                    <tr key={log.id}>
                      <td>{log.step}</td>
                      <td><StatusBadge status={log.status} /></td>
                      <td style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        {log.details ? JSON.stringify(log.details) : "—"}
                      </td>
                      <td style={{ color: "var(--text-muted)" }}>{new Date(log.created_at).toLocaleString("ru")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  );
}
