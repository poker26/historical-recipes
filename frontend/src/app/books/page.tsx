"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Book } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

export default function BooksPage() {
  const [books, setBooks] = useState<Book[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterDomain, setFilterDomain] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [showUpload, setShowUpload] = useState(false);

  const fetchBooks = () => {
    setLoading(true);
    api.listBooks({
      domain: filterDomain || undefined,
      status: filterStatus || undefined,
    }).then(setBooks).catch(console.error).finally(() => setLoading(false));
  };

  useEffect(fetchBooks, [filterDomain, filterStatus]);

  const handleUpload = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const formData = new FormData(form);
    try {
      await api.uploadBook(formData);
      form.reset();
      setShowUpload(false);
      fetchBooks();
    } catch (err) {
      alert(`Upload failed: ${err}`);
    }
  };

  return (
    <>
      <div className="page-header">
        <h1>Library</h1>
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
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="btn btn-primary">Upload</button>
              <button type="button" className="btn btn-outline" onClick={() => setShowUpload(false)}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      <div className="search-bar">
        <select value={filterDomain} onChange={(e) => setFilterDomain(e.target.value)} style={{ width: 160 }}>
          <option value="">All domains</option>
          <option value="recipes">Recipes</option>
          <option value="herbalism">Herbalism</option>
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
        ) : books.length === 0 ? (
          <div className="empty">No books found</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Author</th>
                  <th>Year</th>
                  <th>Domain</th>
                  <th>PDF Type</th>
                  <th>Language</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {books.map((book) => (
                  <tr key={book.id}>
                    <td><Link href={`/books/${book.id}`}>{book.title}</Link></td>
                    <td>{book.author || "—"}</td>
                    <td>{book.year || "—"}</td>
                    <td><span className={`badge ${book.domain === "recipes" ? "badge-blue" : "badge-green"}`}>{book.domain}</span></td>
                    <td><span className="badge badge-gray">{book.pdf_type}</span></td>
                    <td>{book.language === "pre_reform_ru" ? "Pre-reform" : "Modern"}</td>
                    <td><StatusBadge status={book.status} /></td>
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
