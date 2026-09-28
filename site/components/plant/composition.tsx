// Состав: группы веществ с частью растения и книгой, ниже «Состав → действие»
// с дисклеймером бэкенда дословно.
import Link from "next/link";
import { fmtInt, pluralRu } from "../../lib/api";
import { capFirst, type CompoundGroup, type CompoundInsights, type SpeciesCard } from "../../lib/api-plant";
import { Block, More } from "./bits";

const nSubstances = (n: number) => `${fmtInt(n)} ${pluralRu(n, "вещество", "вещества", "веществ")}`;
const nBooksGenPlain = (n: number) => `${fmtInt(n)} ${pluralRu(n, "книга", "книги", "книг")}`;

function GroupCard({ g }: { g: CompoundGroup }) {
  return (
    <div className="pc-cmp-group">
      <h3>
        {g.group ? capFirst(g.group) : "Другие вещества"} <span className="muted small">{fmtInt(g.itemsTotal)}</span>
      </h3>
      <ul className="pc-cmp-list">
        {g.items.map((it, i) => (
          <li key={i}>
            {it.compoundId ? <Link href={`/compounds/${it.compoundId}`} className="pc-cmp-name">{it.name}</Link> : <span className="pc-cmp-name">{it.name}</span>}
            {it.parts.length ? <span className="muted"> ({it.parts.join(", ")})</span> : null}
            {it.sources.length ? (
              <span className="pc-cmp-src">
                {it.sources.map((s, j) => (
                  <span key={j}>
                    {j > 0 ? "; " : null}
                    {s.bookId ? <Link href={`/library/${s.bookId}`}>{s.book}</Link> : s.book}
                    {s.year && !(s.book ?? "").includes(String(s.year)) ? `, ${s.year}` : ""}
                  </span>
                ))}
                {it.sourcesTotal > it.sources.length ? ` и ещё ${nBooksGenPlain(it.sourcesTotal - it.sources.length)}` : ""}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {g.itemsTotal > g.items.length ? (
        <p className="muted small">И ещё {nSubstances(g.itemsTotal - g.items.length)} этой группы.</p>
      ) : null}
    </div>
  );
}

export function CompoundsBlock({ card, insights }: { card: SpeciesCard; insights: CompoundInsights | null }) {
  const groups = card.compoundGroups;
  const rows = (insights?.insights ?? []).filter((i) => i.compound?.name && i.action?.name);
  if (!groups.length && !rows.length) return null;
  const first = groups.slice(0, 8);
  const rest = groups.slice(8);
  const hidden = card.compoundGroupsTotal - groups.length;
  return (
    <Block
      id="compounds"
      title="Состав"
      lead={
        groups.length
          ? `Химики и фармакогносты нашли в этом ${card.kingdom === "гриб" ? "грибе" : "растении"} ${nSubstances(card.compoundsTotal)}. Они разбиты по группам, у каждого вещества указаны часть и книга.`
          : undefined
      }
    >
      {first.length ? (
        <div className="pc-cmp-grid">
          {first.map((g, i) => (
            <GroupCard key={i} g={g} />
          ))}
        </div>
      ) : null}
      {rest.length ? (
        <More summary={`ещё ${fmtInt(rest.length + hidden)} ${pluralRu(rest.length + hidden, "группа", "группы", "групп")} веществ`}>
          <div className="pc-cmp-grid">
            {rest.map((g, i) => (
              <GroupCard key={i} g={g} />
            ))}
          </div>
          {hidden > 0 ? (
            <p className="muted small">
              И ещё {fmtInt(hidden)} {pluralRu(hidden, "небольшая группа", "небольшие группы", "небольших групп")}. Их вещества найдёшь в книгах из{" "}
              <a href="#sources">списка источников</a>.
            </p>
          ) : null}
        </More>
      ) : null}
      {rows.length ? (
        <div className="pc-insights">
          <h3>Какие действия совпадают с составом</h3>
          <p className="muted small">
            Растения, в которых есть это вещество, книги часто применяют с таким действием. Это совпадение по книгам,
            а не доказанный механизм. Связь тем сильнее, чем меньше шанс, что совпадение случайное.
          </p>
          <div className="pc-table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Вещество</th>
                  <th>Действие</th>
                  <th>Растений</th>
                  <th>Связь</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.compound.name}</td>
                    <td>
                      <Link href={`/actions/${encodeURIComponent(r.action.name)}`}>{r.action.name}</Link>
                    </td>
                    <td>
                      {fmtInt(r.support)}
                      {r.n_plants_with_compound ? ` из ${fmtInt(r.n_plants_with_compound)}` : ""}
                    </td>
                    <td>{r.strength ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="footnote pc-note">Это не медицинская рекомендация.</p>
        </div>
      ) : null}
    </Block>
  );
}
