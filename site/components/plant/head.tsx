// Шапка карточки: фото с подписью (или гравюра, или лист), имена, семейство,
// плашка безопасности, счёт книг и цитат, кнопки и оглавление по якорям.
import Link from "next/link";
import type { ReactNode } from "react";
import { Crumbs, LeafGlyph, PhotoCredit, Safety } from "../common";
import { RUSTORE_URL } from "../../app/lib";
import { largePhoto, capFirst, type Photo, type SafetyInfo } from "../../lib/api-plant";
import { safetyText, safetyView } from "./bits";

export function PlantPhoto({ name, photo, plate }: { name: string; photo: Photo | null; plate: string | null }) {
  // Фото без автора и лицензии не показываем: подпись обязательна.
  if (photo && (photo.attribution || photo.license)) {
    return (
      <div>
        <div className="photo">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={largePhoto(photo.url)} alt={name} />
        </div>
        <PhotoCredit attribution={photo.attribution} license={photo.license} source={photo.source} />
      </div>
    );
  }
  if (plate) {
    return (
      <div>
        <div className="photo plate">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={plate} alt={`${name}, гравюра`} />
        </div>
        <div className="credit">Гравюра из коллекции «Что растёт»</div>
      </div>
    );
  }
  return (
    <div>
      <div className="photo pc-photo-empty" aria-hidden="true">
        <LeafGlyph />
      </div>
      <div className="credit">Фотографии этого вида в атласе пока нет.</div>
    </div>
  );
}

export function SafetyPanel({ safety, isToxic, extra }: { safety: SafetyInfo | null; isToxic: boolean; extra?: ReactNode }) {
  const v = safetyView(safety, isToxic);
  const edible = (safety?.edible_parts ?? []).filter(Boolean);
  const dangerous = (safety?.dangerous_parts ?? []).filter(Boolean);
  return (
    <div className="pc-safety">
      <Safety level={v.level} title={v.title} text={safetyText(safety)} />
      {edible.length ? (
        <div className="pc-parts">
          <span className="muted">Можно есть:</span>
          {edible.map((p, i) => (
            <span key={i} className="chip chip-leaf">{p}</span>
          ))}
        </div>
      ) : null}
      {dangerous.length ? (
        <div className="pc-parts">
          <span className="muted">Опасны:</span>
          {dangerous.map((p, i) => (
            <span key={i} className="chip chip-danger">{p}</span>
          ))}
        </div>
      ) : null}
      {safety?.deadly_twin ? <p className="pc-twin">Опасный двойник: {safety.deadly_twin}</p> : null}
      {extra}
    </div>
  );
}

export type PlantHeadProps = {
  name: string;
  latin: string | null;
  nameModern?: string | null;
  family?: string | null;
  familyLatin?: string | null;
  kingdom?: string | null;
  badge?: string | null;
  photo: Photo | null;
  plate: string | null;
  safety: ReactNode;
  stats?: string | null;
  lines?: ReactNode;
  showSources?: boolean;
};

export function PlantHead(p: PlantHeadProps) {
  const famParam = p.familyLatin || p.family;
  const crumbs: { href?: string; label: string }[] = [{ href: "/atlas", label: "Атлас" }];
  if (p.family && famParam) crumbs.push({ href: `/atlas?family=${encodeURIComponent(famParam)}`, label: capFirst(p.family) });
  crumbs.push({ label: p.name });
  return (
    <>
      <Crumbs items={crumbs} />
      <section className="plant-head pc-head">
        <PlantPhoto name={p.name} photo={p.photo} plate={p.plate} />
        <div className="pc-head-text">
          {p.badge ? <span className="chip chip-mist">{p.badge}</span> : null}
          <h1>{p.name}</h1>
          {p.nameModern ? <p className="pc-modern">Современное название: {p.nameModern}</p> : null}
          {p.latin ? <div className="latin pc-latin">{p.latin}</div> : null}
          {p.family ? (
            <div className="pc-family">
              Семейство{" "}
              {famParam ? <Link href={`/atlas?family=${encodeURIComponent(famParam)}`}>{p.family}</Link> : p.family}
              {p.familyLatin ? <span className="latin"> · {p.familyLatin}</span> : null}
            </div>
          ) : null}
          {p.lines}
          {p.safety}
          {p.stats ? <p className="pc-stats">{p.stats}</p> : null}
          <div className="pc-actions">
            <a href={RUSTORE_URL} target="_blank" rel="noopener" className="btn btn-primary btn-sm">Определить в приложении</a>
            {p.showSources !== false ? <a href="#sources" className="btn btn-ghost btn-sm">Источники</a> : null}
          </div>
        </div>
      </section>
    </>
  );
}

export function Toc({ items }: { items: { id: string; label: string }[] }) {
  if (items.length < 2) return null;
  return (
    <nav className="toc pc-toc" aria-label="Разделы карточки">
      {items.map((i) => (
        <a key={i.id} href={`#${i.id}`}>{i.label}</a>
      ))}
    </nav>
  );
}
