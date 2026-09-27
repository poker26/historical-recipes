// Таблица ассоциаций «вещество ↔ действие» и «показание ↔ вещество».
// Это совпадение по корпусу через растение, а не механизм: подпись обязательна.
import Link from "next/link";
import { fmtInt, plantHref } from "../../lib/api";
import { liftText, pValueText, type Assoc } from "../../lib/api-reference";

type Target = "action" | "compound" | "indication";

function targetHref(kind: Target, row: { id?: string | null; name: string }): string | null {
  if (kind === "action") return `/actions/${encodeURIComponent(row.name)}`;
  if (!row.id) return null;
  return kind === "compound" ? `/compounds/${row.id}` : `/indications/${row.id}`;
}

export function AssocTable({ data, target, targetLabel, sourceLabel }: {
  data: Assoc;
  target: Target;
  /** Заголовок первого столбца: «Действие», «Вещество». */
  targetLabel: string;
  /** Чьи растения считаем: «с рутином», «при кашле» (для подписи столбца). */
  sourceLabel: string;
}) {
  const rows = data.results ?? [];
  return (
    <>
      <table className="table rf-table rf-assoc">
        <thead>
          <tr>
            <th scope="col">{targetLabel}</th>
            <th scope="col">Растений {sourceLabel}</th>
            <th scope="col">Чаще среднего</th>
            <th scope="col">Шанс случайности</th>
            <th scope="col">Например</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const href = targetHref(target, r);
            return (
              <tr key={r.id ?? `${r.name}-${i}`}>
                <td data-label={targetLabel}>{href ? <Link href={href}>{r.name}</Link> : r.name}</td>
                <td data-label="Растений">{fmtInt(r.support)} из {fmtInt(r.source_plants)}</td>
                <td data-label="Чаще среднего">{liftText(r.lift)}</td>
                <td data-label="Шанс случайности" title={`p = ${r.p_value.toExponential(1)}`}>{pValueText(r.p_value)}</td>
                <td data-label="Например">
                  {r.plants.slice(0, 3).map((p, j) => (
                    <span key={p.id}>
                      {j ? ", " : ""}
                      <Link href={plantHref(p.id, p.name_latin)}>{p.name}</Link>
                    </span>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {data.note ? (
        <p className="small muted rf-note" lang="en">{data.note}</p>
      ) : null}
    </>
  );
}
