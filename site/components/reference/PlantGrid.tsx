// Плитки видов для страниц понятий и список имён для хвоста без плиток.
import Link from "next/link";
import { Tile } from "../common";
import { fmtInt, plantHref, pluralRu } from "../../lib/api";
import { cap, type PlantSummary } from "../../lib/api-reference";

/** Метки безопасности на плитке: те же слова, что на главной. Молчание источников не метим. */
export function plantTags(p: PlantSummary): { label: string; warn?: boolean }[] {
  const tags: { label: string; warn?: boolean }[] = [];
  if (p.safety_level === 4) tags.push({ label: "смертельно ядовито", warn: true });
  else if (p.safety_level === 3) tags.push({ label: "осторожно", warn: true });
  else if (p.is_toxic && p.safety_level == null) tags.push({ label: "ядовито", warn: true });
  if (p.deadly_twin) tags.push({ label: "есть опасный двойник", warn: true });
  return tags;
}

/** Подпись плитки: семейство. Общее число записей о применении здесь не показываем:
 *  на странице понятия его легко принять за число записей именно об этом понятии. */
function metaFor(p: PlantSummary): string | null {
  return p.family ? cap(p.family) : null;
}

export function PlantGrid({ plants }: { plants: PlantSummary[] }) {
  return (
    <div className="tiles">
      {plants.map((p) => (
        <Tile
          key={p.id}
          href={plantHref(p.id, p.name_latin)}
          name={p.name}
          latin={p.name_latin}
          photo={p.photo_url}
          meta={metaFor(p)}
          tags={plantTags(p)}
        />
      ))}
    </div>
  );
}

type NamedPlant = { id: string; name: string; name_latin?: string | null; parts?: string[] | null };

const CYR = /^[А-ЯЁа-яё]/;

/** Хвост списка: растения, которых нет среди плиток. Сначала русские имена, потом латинские. */
export function PlantNameList({ items, max = 150 }: { items: NamedPlant[]; max?: number }) {
  const sorted = [...items]
    .filter((p) => p.name)
    .sort((a, b) => Number(!CYR.test(a.name)) - Number(!CYR.test(b.name)) || a.name.localeCompare(b.name, "ru"));
  const shown = sorted.slice(0, max);
  const rest = sorted.length - shown.length;
  return (
    <>
      <ul className="rf-names">
        {shown.map((p) => (
          <li key={p.id}>
            <Link href={plantHref(p.id, p.name_latin)}>{p.name}</Link>
            {p.parts?.length ? <span className="muted small"> · {p.parts.slice(0, 3).join(", ")}</span> : null}
          </li>
        ))}
      </ul>
      {rest > 0 ? (
        <p className="small muted">
          И ещё {fmtInt(rest)} {pluralRu(rest, "растение", "растения", "растений")} в корпусе.
        </p>
      ) : null}
    </>
  );
}
