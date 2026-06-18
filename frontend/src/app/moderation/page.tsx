"use client";

import { useEffect, useState, useCallback } from "react";
import { api, ModDevice } from "@/lib/api";

export default function ModerationPage() {
  const [devices, setDevices] = useState<ModDevice[]>([]);
  const [onlyCustom, setOnlyCustom] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api
      .moderationNicknames(onlyCustom)
      .then((r) => setDevices(r.devices))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [onlyCustom]);

  useEffect(() => { load(); }, [load]);

  const toggle = async (d: ModDevice) => {
    setBusy(d.device_key);
    try {
      await api.moderationBlock(d.device_key, !d.blocked);
      load();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const blockedCount = devices.filter((d) => d.blocked).length;

  return (
    <>
      <div className="page-header">
        <h1>Модерация имён</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginLeft: "auto" }}>
          <span style={{ color: "var(--text-muted)" }}>{blockedCount} заблокировано</span>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={onlyCustom} onChange={(e) => setOnlyCustom(e.target.checked)} />
            только кастомные ники
          </label>
          <button className="btn btn-outline btn-sm" onClick={load}>↻ Обновить</button>
        </div>
      </div>

      <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 12 }}>
        Заблокированные скрыты из рейтинга, публичного профиля, ленты и подписок. Ник остаётся в БД.
      </p>

      {loading ? (
        <p>Загрузка…</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>Имя</th><th>handle</th><th>Последняя активность</th><th>Статус</th><th></th></tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <tr key={d.device_key} style={d.blocked ? { opacity: 0.55 } : undefined}>
                <td>{d.nickname || <span style={{ color: "var(--text-muted)" }}>— (авто)</span>}</td>
                <td><code>{d.handle}</code></td>
                <td>{d.last_seen ? new Date(d.last_seen).toLocaleString("ru-RU") : "—"}</td>
                <td>
                  {d.blocked
                    ? <span className="badge badge-red">заблокирован</span>
                    : <span className="badge badge-gray">ок</span>}
                </td>
                <td>
                  <button
                    className="btn btn-outline btn-sm"
                    disabled={busy === d.device_key}
                    onClick={() => toggle(d)}
                  >
                    {d.blocked ? "Разблокировать" : "Заблокировать"}
                  </button>
                </td>
              </tr>
            ))}
            {devices.length === 0 && (
              <tr><td colSpan={5} style={{ color: "var(--text-muted)" }}>Пусто</td></tr>
            )}
          </tbody>
        </table>
      )}
    </>
  );
}
