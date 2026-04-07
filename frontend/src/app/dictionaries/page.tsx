"use client";

import { useEffect, useState } from "react";
import { api, DictionaryTerm } from "@/lib/api";

const CATEGORIES = ["", "measurements", "plants", "orthography", "techniques"];

export default function DictionariesPage() {
  const [terms, setTerms] = useState<DictionaryTerm[]>([]);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const fetchTerms = () => {
    setLoading(true);
    if (search) {
      api.lookupTerm(search).then(setTerms).catch(console.error).finally(() => setLoading(false));
    } else {
      api.listTerms(category || undefined).then(setTerms).catch(console.error).finally(() => setLoading(false));
    }
  };

  useEffect(fetchTerms, [category, search]);

  const handleAdd = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form)) as Record<string, string>;
    try {
      await api.createTerm({
        category: data.category,
        term_old: data.term_old,
        term_modern: data.term_modern,
        context: data.context || null,
        source_book_id: null,
      });
      form.reset();
      setShowAdd(false);
      fetchTerms();
    } catch (err) {
      alert(`Error: ${err}`);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this term?")) return;
    await api.deleteTerm(id);
    fetchTerms();
  };

  return (
    <>
      <div className="page-header">
        <h1>Dictionaries</h1>
        <button className="btn btn-primary" onClick={() => setShowAdd(!showAdd)}>+ Add Term</button>
      </div>

      {showAdd && (
        <div className="card" style={{ marginBottom: 20 }}>
          <form onSubmit={handleAdd}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label>Category</label>
                <select name="category" required>
                  <option value="measurements">Measurements</option>
                  <option value="plants">Plants</option>
                  <option value="orthography">Orthography</option>
                  <option value="techniques">Techniques</option>
                </select>
              </div>
              <div className="form-group">
                <label>Old term</label>
                <input name="term_old" required placeholder="e.g. золотникъ" />
              </div>
              <div className="form-group">
                <label>Modern equivalent</label>
                <input name="term_modern" required placeholder="e.g. 4.27 г" />
              </div>
              <div className="form-group">
                <label>Context (optional)</label>
                <input name="context" placeholder="e.g. unit of weight" />
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="btn btn-primary btn-sm">Save</button>
              <button type="button" className="btn btn-outline btn-sm" onClick={() => setShowAdd(false)}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      <div className="search-bar">
        <input placeholder="Search terms..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={category} onChange={(e) => { setCategory(e.target.value); setSearch(""); }} style={{ width: 180 }}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c || "All categories"}</option>
          ))}
        </select>
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : terms.length === 0 ? (
          <div className="empty">No terms found. Add dictionary terms to enable text normalization.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Category</th><th>Old Term</th><th>Modern</th><th>Context</th><th></th></tr>
              </thead>
              <tbody>
                {terms.map((t) => (
                  <tr key={t.id}>
                    <td><span className="badge badge-blue">{t.category}</span></td>
                    <td style={{ fontWeight: 600 }}>{t.term_old}</td>
                    <td>{t.term_modern}</td>
                    <td style={{ color: "var(--text-muted)" }}>{t.context || "—"}</td>
                    <td>
                      <button className="btn btn-danger btn-sm" onClick={() => handleDelete(t.id)}>Delete</button>
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
