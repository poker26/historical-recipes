"use client";

import { useEffect, useState, useCallback } from "react";
import { api, QualityFinding, QualitySummaryRow } from "@/lib/api";
import Pagination from "@/components/Pagination";

const PAGE_SIZE = 50;

const sevBadge = (s: string) =>
  s === "P0" ? "badge badge-red" : s === "P1" ? "badge badge-blue" : "badge badge-gray";

export default function QualityPage() {
  const [summary, setSummary] = useState<QualitySummaryRow[]>([]);
  const [findings, setFindings] = useState<QualityFinding[]>([]);
  const [checkId, setCheckId] = useState("");
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("open");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const loadSummary = useCallback(() => {
    api.qualitySummary().then(setSummary).catch(console.error);
  }, []);

  const loadFindings = useCallback(() => {
    setLoading(true);
    api
      .qualityFindings({ check_id: checkId || undefined, severity: severity || undefined, status: status || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      .then(setFindings)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [checkId, severity, status, page]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { loadFindings(); }, [loadFindings]);

  // total open across all checks (for the header)
  const totalOpen = summary.filter((r) => r.status === "open").reduce((a, r) => a + r.count, 0);

  // distinct check ids (for the filter dropdown), from the summary
  const checkIds = Array.from(new Set(summary.map((r) => r.check_id))).sort();

  const act = async (fn: () => Promise<unknown>, label: string) => {
    setBusy(label);
    try { await fn(); loadFindings(); loadSummary(); }
    catch (e) { alert(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  };

  return (
    <>
      <div className="page-header">
        <h1>Качество данных</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginLeft: "auto" }}>
          {busy && <span style={{ color: "var(--text-muted)", fontSize: 13 }}>{busy}…</span>}
          <span style={{ color: "var(--text-muted)" }}>{totalOpen} открытых</span>
          <button className="btn btn-outline btn-sm" disabled={!!busy}
            onClick={() => act(() => api.qualitySweep(), "Прогоняю свип")}>↻ Свип</button>
          <button className="btn btn-outline btn-sm" disabled={!!busy}
            onClick={() => act(() => api.qualityResolveTaxonomy(1000), "Резолв таксономии")}>🌍 Таксономия</button>
        </div>
      </div>

      {/* How-to legend — the triage model isn't obvious from buttons alone. */}
      <div className="card" style={{ marginBottom: 16, fontSize: 13, color: "var(--text-muted)", lineHeight: 1.6 }}>
        Каждая строка — это что линтер <b>заподозрил</b> (кандидат, не приговор; часть реальные, часть ложные).
        Кнопки сортируют кучу, данные при этом не меняются:
        <br />
        <span className="badge badge-green">✓ Реальная</span> — да, проблема есть (пометить «проверено, надо чинить»). &nbsp;
        <span className="badge badge-gray">✕ Ложная</span> — линтер ошибся, у нас верно (скрыть). &nbsp;
        <span className="badge badge-red">⚡ Исправить</span> — где есть авто-фикс, чинит данные в один клик (ИЗМЕНЯЕТ данные).
        <br />
        Подтверждённые/отклонённые свип больше не поднимает. Массовые исправления (как «грибы») делаются отдельно.
      </div>

      {/* Summary: one row per check, open-count by severity. Click to filter. */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Проверка</th><th>Severity</th><th>Открыто</th><th>Подтверждено</th><th>Отклонено</th><th>Исправлено</th></tr></thead>
            <tbody>
              {checkIds.map((cid) => {
                const rows = summary.filter((r) => r.check_id === cid);
                const c = (st: string) => rows.filter((r) => r.status === st).reduce((a, r) => a + r.count, 0);
                const sev = rows[0]?.severity ?? "";
                return (
                  <tr key={cid} style={{ cursor: "pointer" }} onClick={() => { setCheckId(cid); setPage(0); setStatus("open"); }}>
                    <td><code>{cid}</code></td>
                    <td><span className={sevBadge(sev)}>{sev}</span></td>
                    <td><b>{c("open")}</b></td>
                    <td style={{ color: "var(--text-muted)" }}>{c("confirmed")}</td>
                    <td style={{ color: "var(--text-muted)" }}>{c("dismissed")}</td>
                    <td style={{ color: "var(--text-muted)" }}>{c("fixed")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Filters */}
      <div className="search-bar" style={{ gap: 8, flexWrap: "wrap" }}>
        <select value={checkId} onChange={(e) => { setCheckId(e.target.value); setPage(0); }} style={{ width: 260 }}>
          <option value="">Все проверки</option>
          {checkIds.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(0); }} style={{ width: 110 }}>
          <option value="">Любая</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option>
        </select>
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }} style={{ width: 150 }}>
          <option value="open">Открытые</option><option value="confirmed">Подтверждённые</option>
          <option value="dismissed">Отклонённые</option><option value="fixed">Исправленные</option><option value="stale">Устаревшие</option>
        </select>
        {(checkId || severity) && (
          <button className="btn btn-outline btn-sm" onClick={() => { setCheckId(""); setSeverity(""); setPage(0); }}>Сброс</button>
        )}
      </div>

      <div className="card">
        {loading ? (
          <div className="empty"><span className="spinner" /></div>
        ) : findings.length === 0 ? (
          <div className="empty">Нет находок по фильтру.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {findings.map((f) => (
              <div key={f.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px" }}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                  <span className={sevBadge(f.severity)}>{f.severity}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500 }}>{f.title}</div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                      <code>{f.check_id}</code> · {f.entity_type} · <code>{f.entity_id.slice(0, 8)}</code>
                      {f.auto_fixable && <span className="badge badge-green" style={{ marginLeft: 6 }}>auto-fix</span>}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn btn-outline btn-sm" disabled={!!busy}
                      title="Да, это реальная проблема — пометить «проверено, надо чинить». Данные НЕ меняются."
                      onClick={() => act(() => api.qualityTriage(f.id, "confirmed"), "Помечаю реальной")}>✓ Реальная</button>
                    <button className="btn btn-outline btn-sm" disabled={!!busy}
                      title="Ложное срабатывание — у нас на самом деле верно. Скрыть из списка. Данные НЕ меняются."
                      onClick={() => act(() => api.qualityTriage(f.id, "dismissed"), "Помечаю ложной")}>✕ Ложная</button>
                    {f.auto_fixable && (
                      <button className="btn btn-sm" disabled={!!busy} style={{ background: "var(--red)", color: "#fff" }}
                        title="Исправить автоматически — ИЗМЕНИТ данные (например, удалит фейк-рецепт)."
                        onClick={() => { if (confirm("Применить авто-фикс? Это изменит данные.")) act(() => api.qualityApply(f.id), "Применяю фикс"); }}>⚡ Исправить</button>
                    )}
                    {(f.entity_type === "plant" || f.entity_type === "recipe") && (
                      <button className="btn btn-sm" disabled={!!busy} style={{ background: "var(--red)", color: "#fff" }}
                        title="Удалить саму карточку (растение/рецепт) — для мусора, которого вообще не должно быть. Необратимо."
                        onClick={() => { if (confirm(`Удалить ${f.entity_type === "plant" ? "растение" : "рецепт"} «${f.title.slice(0, 40)}…»? Карточка, её факты и qdrant-точка удалятся НЕОБРАТИМО.`)) act(() => api.qualityDeleteEntity(f.id), "Удаляю карточку"); }}>🗑 Удалить</button>
                    )}
                  </div>
                </div>
                {f.evidence && (
                  <details style={{ marginTop: 8 }}>
                    <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--text-muted)" }}>evidence</summary>
                    <pre style={{ fontSize: 11, overflow: "auto", background: "var(--bg)", padding: 8, borderRadius: 6, marginTop: 6 }}>
                      {JSON.stringify(f.evidence, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {!loading && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={page * PAGE_SIZE + findings.length + (findings.length === PAGE_SIZE ? 1 : 0)} onPageChange={setPage} />
      )}
    </>
  );
}
