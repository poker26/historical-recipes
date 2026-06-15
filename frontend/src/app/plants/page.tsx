"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, Plant, PlantFacets } from "@/lib/api";
import Pagination from "@/components/Pagination";

const PAGE_SIZE = 48;

// Read/write list state (search, filters, page) in the URL query string so that
// opening a plant card and pressing Back restores the exact list view — same page
// and filters — instead of resetting to the first page. Uses window.history
// directly (no useSearchParams → no Suspense boundary requirement at build time).
function readParam(key: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(key) || "";
}
function readPageParam(): number {
  const v = parseInt(readParam("page"), 10);
  return Number.isNaN(v) || v < 0 ? 0 : v;
}

export default function PlantsPage() {
  const [plants, setPlants] = useState<Plant[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(readPageParam);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(() => readParam("q"));
  const [compound, setCompound] = useState(() => readParam("compound"));
  const [action, setAction] = useState(() => readParam("action"));
  const [toxicOnly, setToxicOnly] = useState(() => readParam("toxic") === "1");
  const [facets, setFacets] = useState<PlantFacets | null>(null);
  const [enrichMsg, setEnrichMsg] = useState<string | null>(null);

  const startEnrichment = async () => {
    setEnrichMsg("Запускаю обогащение iNaturalist…");
    try {
      await api.runInatEnrichment();
      setEnrichMsg("Обогащение запущено (Temporal). Фото подтянутся по мере обработки.");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setEnrichMsg(msg.includes("409") ? "Обогащение уже выполняется." : `Ошибка: ${msg}`);
    }
  };

  useEffect(() => {
    api.getPlantFacets().then(setFacets).catch(console.error);
  }, []);

  // New search/filter → jump back to the first page. Skipped on the initial
  // mount so a page restored from the URL (Back from a card) isn't clobbered.
  const didMount = useRef(false);
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return; }
    setPage(0);
  }, [search, compound, action, toxicOnly]);

  // Reflect the current view in the URL (replaceState = no new history entry per
  // click), so navigating into a card and pressing Back returns to this exact page.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (compound) params.set("compound", compound);
    if (action) params.set("action", action);
    if (toxicOnly) params.set("toxic", "1");
    if (page > 0) params.set("page", String(page));
    const qs = params.toString();
    const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(window.history.state, "", url);
  }, [search, compound, action, toxicOnly, page]);

  useEffect(() => {
    setLoading(true);
    api.listPlants({
      q: search || undefined,
      compound: compound || undefined,
      action: action || undefined,
      is_toxic: toxicOnly || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then(({ items, total }) => { setPlants(items); setTotal(total); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [search, compound, action, toxicOnly, page]);

  const hasFilters = !!(search || compound || action || toxicOnly);

  return (
    <>
      <div className="page-header">
        <h1>Herbarium</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginLeft: "auto" }}>
          {enrichMsg && <span style={{ color: "var(--text-muted)", fontSize: 13 }}>{enrichMsg}</span>}
          <span style={{ color: "var(--text-muted)" }}>{total} plants</span>
          <button
            className="btn btn-outline btn-sm"
            onClick={startEnrichment}
            title="Подтянуть недостающие фото растений из iNaturalist (фоновый Temporal-процесс)"
          >
            📷 iNaturalist
          </button>
        </div>
      </div>

      <div className="search-bar" style={{ flexWrap: "wrap", gap: 8 }}>
        <input placeholder="Search plants..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={compound} onChange={(e) => setCompound(e.target.value)} style={{ width: 200 }}>
          <option value="">Any compound group</option>
          {facets?.compound_groups.map((g) => (
            <option key={g.value} value={g.value}>{g.value} ({g.count})</option>
          ))}
        </select>
        <select value={action} onChange={(e) => setAction(e.target.value)} style={{ width: 220 }}>
          <option value="">Any medicinal action</option>
          {facets?.actions.map((a) => (
            <option key={a.value} value={a.value}>{a.value} ({a.count})</option>
          ))}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, color: "var(--text-muted)" }}>
          <input type="checkbox" checked={toxicOnly} onChange={(e) => setToxicOnly(e.target.checked)} />
          Toxic only
        </label>
        {hasFilters && (
          <button
            className="btn btn-outline btn-sm"
            onClick={() => { setSearch(""); setCompound(""); setAction(""); setToxicOnly(false); }}
          >
            Clear
          </button>
        )}
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : plants.length === 0 ? (
          <div className="empty">No plants in the herbarium yet. Process herbalism books first.</div>
        ) : (
          <div className="card-grid">
            {plants.map((p) => (
              <Link href={`/plants/${p.id}`} key={p.id} className="card" style={{ textDecoration: "none", color: "inherit" }}>
                {p.photo_url && (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={p.photo_url.replace("/medium.", "/square.")}
                    alt={p.name}
                    title={p.photo_attribution || undefined}
                    loading="lazy"
                    style={{ width: "100%", height: 130, objectFit: "cover", borderRadius: 6, marginBottom: 10, background: "var(--border)" }}
                  />
                )}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                  <h3 style={{ margin: 0 }}>{p.name}</h3>
                  {p.is_toxic && <span className="badge badge-red" title="Toxic plant">⚠ toxic</span>}
                </div>
                {p.name_modern && p.name_modern !== p.name && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>совр. {p.name_modern}</div>
                )}
                {p.name_latin && <div style={{ fontSize: 13, fontStyle: "italic", color: "var(--text-muted)" }}>{p.name_latin}</div>}
                {(p.family || p.family_latin) && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                    {p.family}{p.family && p.family_latin ? " · " : ""}{p.family_latin}
                  </div>
                )}
                {p.parts_used && p.parts_used.length > 0 && (
                  <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {p.parts_used.map((part) => (
                      <span key={part} className="badge badge-green">{part}</span>
                    ))}
                  </div>
                )}
                {p.names_historical && p.names_historical.length > 0 && (
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
                    Historical: {p.names_historical.join(", ")}
                  </div>
                )}
                {p.uses_count > 0 && (
                  <div style={{ fontSize: 12, color: "var(--blue)", marginTop: 6 }}>
                    {p.uses_count} medicinal use{p.uses_count === 1 ? "" : "s"}
                  </div>
                )}
              </Link>
            ))}
          </div>
        )}
      </div>

      {!loading && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      )}
    </>
  );
}
