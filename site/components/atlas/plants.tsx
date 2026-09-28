// Плитки и таблица видов для атласа, страниц «растения при…» и поиска.
// Разметка та же, что у общей плитки Tile (классы .tile*), плюс подпись под снимком:
// у каждого фото iNaturalist или Викимедиа видны автор и лицензия, без подписи фото не показываем.
import Link from "next/link";
import { LeafGlyph } from "../common";
import { fmtInt, plantHref, pluralRu } from "../../lib/api";
import {
  creditShort,
  displayName,
  familyShort,
  modernName,
  plantTags,
  safetyInfo,
  type PlantSummary,
  type TileTag,
} from "../../lib/api-atlas";

export function usesLabel(n?: number | null): string | null {
  if (!n) return null;
  return `${fmtInt(n)} ${pluralRu(n, "запись", "записи", "записей")} о применении`;
}

export function TagList({ tags }: { tags: TileTag[] }) {
  if (!tags.length) return null;
  return (
    <div className="tile-tags">
      {tags.map((t, i) => (
        <span key={i} className={"tag" + (t.tone === "warn" ? " tag-warn" : t.tone === "mist" ? " tag-mist" : "")}>
          {t.label}
        </span>
      ))}
    </div>
  );
}

export function PlantTile({ p, noPhotoTag, note }: { p: PlantSummary; noPhotoTag?: boolean; note?: string | null }) {
  const name = displayName(p.name);
  const modern = modernName(p);
  const credit = p.photo_url ? creditShort(p.photo_attribution) : null;
  // Фото без подписи автора не показываем: вместо него лист-заглушка.
  const photo = p.photo_url && credit ? p.photo_url : null;
  const fam = familyShort(p);
  const meta = [usesLabel(p.uses_count), fam ? `семейство ${fam}` : null].filter(Boolean).join(", ");
  return (
    <Link href={plantHref(p.id, p.name_latin)} className="tile" title={p.name !== name ? p.name : undefined}>
      <div className="tile-photo">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {photo ? <img src={photo} alt={name} loading="lazy" /> : <LeafGlyph />}
      </div>
      {photo ? (
        <div className="tile-credit" title={p.photo_attribution ?? undefined}>
          {credit}
        </div>
      ) : null}
      <div className="tile-name">{name}</div>
      {p.name_latin ? <div className="tile-latin">{p.name_latin}</div> : null}
      {modern ? <div className="tile-meta">по-современному {modern}</div> : null}
      {note ? <div className="tile-meta tile-note">{note}</div> : null}
      {meta ? <div className="tile-meta">{meta}</div> : null}
      <TagList tags={plantTags(p, { noPhoto: noPhotoTag })} />
    </Link>
  );
}

export function PlantGrid({
  items,
  noPhotoTag,
  notes,
}: {
  items: PlantSummary[];
  noPhotoTag?: boolean;
  notes?: Record<string, string | null | undefined>;
}) {
  return (
    <div className="tiles atlas-tiles">
      {items.map((p) => (
        <PlantTile key={p.id} p={p} noPhotoTag={noPhotoTag} note={notes?.[p.id] ?? null} />
      ))}
    </div>
  );
}

/** Табличный вид: имя, латынь, семейство, царство, число применений, безопасность. */
export function PlantTable({ items }: { items: PlantSummary[] }) {
  return (
    <div className="atlas-table-wrap">
      <table className="table atlas-table">
        <thead>
          <tr>
            <th>Имя</th>
            <th className="c-lat">Латынь</th>
            <th className="c-fam">Семейство</th>
            <th className="c-king">Царство</th>
            <th className="c-num">Применений</th>
            <th>Безопасность</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => {
            const s = safetyInfo(p);
            return (
              <tr key={p.id}>
                <td>
                  <Link href={plantHref(p.id, p.name_latin)} className="atlas-tname">
                    {displayName(p.name)}
                  </Link>
                  {p.name_latin ? <div className="latin small c-lat-inline">{p.name_latin}</div> : null}
                  {p.uses_count ? <div className="muted small c-num-inline">{usesLabel(p.uses_count)}</div> : null}
                </td>
                <td className="c-lat latin">{p.name_latin || "—"}</td>
                <td className="c-fam">{familyShort(p) ?? "—"}</td>
                <td className="c-king">{p.kingdom === "гриб" ? "гриб" : "растение"}</td>
                <td className="c-num">{fmtInt(p.uses_count ?? 0)}</td>
                <td>
                  <span className={`atlas-safety s-${s.kind}`}>{s.label}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Переключатель из двух-трёх положений (сортировка, плитки или таблица). */
export function Seg({ items, label }: { items: { href: string; label: string; on: boolean; title?: string }[]; label: string }) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {items.map((it) =>
        it.on ? (
          <span key={it.label} className="on" aria-current="true" title={it.title}>
            {it.label}
          </span>
        ) : (
          <Link key={it.label} href={it.href} rel="nofollow" title={it.title}>
            {it.label}
          </Link>
        ),
      )}
    </span>
  );
}

export function PhotoSourcesNote() {
  return (
    <p className="footnote atlas-photonote">
      Фотографии взяты из iNaturalist и Викимедиа. Автор и лицензия подписаны под каждым снимком, а полную строку
      прав видно, если навести на подпись.
    </p>
  );
}
