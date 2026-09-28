// Сбор и «где растёт»: записи книг, канонические биотопы и живые наблюдения
// iNaturalist с сезонностью и подписями авторов снимков.
import { Chips, MONTHS_GEN_RU, MONTHS_RU, PhotoCredit } from "../common";
import { fmtInt, pluralRu } from "../../lib/api";
import { biotopeLabel } from "../../lib/api-atlas";
import { OBS_PLACE_RU, canonParts, mediumPhoto, type FieldView, type Observations, type SpeciesCard } from "../../lib/api-plant";
import { Block, FactList } from "./bits";

const MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];
const MONTHS_ABBR = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function dateRu(iso?: string | null): string | null {
  const m = (iso ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  const mi = Number(m[2]) - 1;
  return mi >= 0 && mi < 12 ? `${Number(m[3])} ${MONTHS_GEN_RU[mi]} ${m[1]}` : null;
}

export function HarvestBlock({ card, field }: { card: SpeciesCard; field: FieldView | null }) {
  const h = field?.harvest ?? null;
  const parts = Array.from(new Set((h?.parts ?? []).filter(Boolean).flatMap((p) => canonParts(p))));
  const seasons = (h?.seasons ?? []).filter(Boolean);
  const where = (h?.where ?? []).filter(Boolean);
  const hasSummary = parts.length || seasons.length || where.length;
  if (!hasSummary && !card.harvests.total) return null;
  return (
    <Block id="harvest" title="Сбор">
      {hasSummary ? (
        <dl className="kv pc-kv">
          {parts.length ? (
            <>
              <dt>Что собирают</dt>
              <dd>{parts.slice(0, 12).join(", ")}</dd>
            </>
          ) : null}
          {seasons.length ? (
            <>
              <dt>Когда</dt>
              <dd>{seasons.slice(0, 6).join("; ")}</dd>
            </>
          ) : null}
          {where.length ? (
            <>
              <dt>Где</dt>
              <dd>{where.slice(0, 4).join("; ")}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
      {card.harvests.total ? <FactList set={card.harvests} /> : null}
    </Block>
  );
}

function InatPanel({ obs }: { obs: Observations }) {
  const total = obs.total_count ?? 0;
  const months = Array.from({ length: 12 }, (_, i) => Number(obs.seasonality?.[String(i + 1)] ?? 0) || 0);
  const max = Math.max(...months);
  const peak = max > 0 ? months.indexOf(max) : -1;
  // Четыре снимка по возможности от разных людей: свежие наблюдения часто идут пачкой от одного автора.
  const usable = (obs.observations ?? []).filter((o) => o.photo_url && (o.photo_attribution || o.photo_license));
  const seen = new Set<string>();
  const photos = [
    ...usable.filter((o) => {
      const who = o.observer || String(o.id);
      if (seen.has(who)) return false;
      seen.add(who);
      return true;
    }),
    ...usable,
  ]
    .filter((o, i, arr) => arr.findIndex((x) => x.id === o.id) === i)
    .slice(0, 4);
  return (
    <div className="card pc-inat">
      <h3>Наблюдения {OBS_PLACE_RU}</h3>
      <p className="pc-inat-lead">
        На iNaturalist {OBS_PLACE_RU} отмечено {fmtInt(total)} {pluralRu(total, "наблюдение", "наблюдения", "наблюдений")} этого вида.
        {peak >= 0 ? ` Чаще всего его замечают в ${MONTHS_PREP[peak]}.` : ""}
      </p>
      {max > 0 ? (
        <div className="pc-season" role="img" aria-label={"Наблюдения по месяцам. " + months.map((n, i) => `${MONTHS_RU[i]} ${n}`).join(", ")}>
          {months.map((n, i) => (
            <div key={i} className="pc-season-col" title={`В ${MONTHS_PREP[i]} ${fmtInt(n)} ${pluralRu(n, "наблюдение", "наблюдения", "наблюдений")}`}>
              <div className="pc-season-track">
                <div className={"pc-season-bar" + (i === peak ? " peak" : "")} style={{ height: `${n ? Math.max(4, Math.round((n / max) * 100)) : 0}%` }} />
              </div>
              <span>{MONTHS_ABBR[i]}</span>
            </div>
          ))}
        </div>
      ) : null}
      {photos.length ? (
        <div className="pc-inat-photos">
          {photos.map((o) => (
            <figure key={o.id}>
              <a href={o.uri || `https://www.inaturalist.org/observations/${o.id}`} target="_blank" rel="noopener">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediumPhoto(o.photo_url as string)} alt="Наблюдение на iNaturalist" loading="lazy" />
              </a>
              <figcaption>
                <PhotoCredit attribution={o.photo_attribution} license={o.photo_license} source="inaturalist" />
                {dateRu(o.observed_on) ? <div className="credit">{dateRu(o.observed_on)}</div> : null}
              </figcaption>
            </figure>
          ))}
        </div>
      ) : null}
      <p className="credit">
        Наблюдения и снимки взяты из iNaturalist.
        {obs.taxon_id ? (
          <>
            {" "}
            <a href={`https://www.inaturalist.org/taxa/${obs.taxon_id}`} target="_blank" rel="noopener">Открыть страницу вида на iNaturalist</a>
          </>
        ) : null}
      </p>
    </div>
  );
}

export function HabitatBlock({ card, field, obs }: { card: SpeciesCard; field: FieldView | null; obs: Observations | null }) {
  const hb = field?.habitat ?? null;
  const biotopes = (hb?.biotopes ?? []).filter((b) => b?.key);
  const summary = hb?.summary?.trim() || null;
  const obsOk = !!obs && !obs.error && (obs.total_count ?? 0) > 0;
  if (!summary && !biotopes.length && !card.habitats.total && !obsOk) return null;
  return (
    <Block id="habitat" title="Где растёт">
      {summary ? <p className="pc-text">{summary}</p> : null}
      {biotopes.length ? (
        <div className="pc-biotopes">
          <span className="pc-label">Где встречается</span>
          <Chips items={biotopes.map((b) => ({ href: `/biotopes/${b.key}`, label: biotopeLabel(b.key), kind: "leaf" as const }))} />
        </div>
      ) : null}
      {card.habitats.total ? <FactList set={card.habitats} /> : null}
      {obsOk && obs ? <InatPanel obs={obs} /> : null}
    </Block>
  );
}
