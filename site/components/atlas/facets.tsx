// Фасеты атласа в боковой колонке и чипы выбранных фильтров над результатами.
// Всё на обычных ссылках: выбранный пункт подсвечен и снимается повторным кликом.
// На узком экране фасеты складываются под кнопку «Фильтры» (чекбокс и CSS, без JS),
// чтобы плитки не уезжали на несколько экранов вниз.
import Link from "next/link";
import type { ReactNode } from "react";
import { fmtInt } from "../../lib/api";
import {
  BIOTOPE_GROUPS,
  EDIBILITY_OPTIONS,
  KINGDOM_LABEL,
  biotopeLabel,
  biotopeWhere,
  conditionPhrase,
  familyFacets,
  familyKey,
  familyLabel,
  type BiotopeFacet,
  type FacetValue,
  type PlantFacets,
} from "../../lib/api-atlas";
import { activeFilterKeys, atlasHref, type AtlasState, type FilterKey } from "./params";

const VISIBLE = 12;

type Item = {
  key: string;
  label: string;
  href: string;
  active: boolean;
  count?: number | null;
  latin?: string | null;
  /** Ссылку на индексируемую страницу (чистый атлас, атлас грибов) не закрываем от роботов. */
  follow?: boolean;
};

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

function FacetLink({ it }: { it: Item }) {
  return (
    <Link
      href={it.href}
      className={it.active ? "active" : undefined}
      rel={it.follow ? undefined : "nofollow"}
      aria-current={it.active ? "true" : undefined}
      title={it.active ? "Снять фильтр" : undefined}
    >
      <span className="facet-label">
        {it.label}
        {it.latin ? <i className="facet-lat"> {it.latin}</i> : null}
      </span>
      <span className="n">{it.active ? "✕" : it.count != null ? fmtInt(it.count) : ""}</span>
    </Link>
  );
}

function FacetList({ items, more }: { items: Item[]; more: (n: number) => string }) {
  const head = items.slice(0, VISIBLE);
  const rest = items.slice(VISIBLE);
  return (
    <>
      {head.map((it) => (
        <FacetLink key={it.key} it={it} />
      ))}
      {rest.length ? (
        <details className="facet-more" open={rest.some((i) => i.active)}>
          <summary>{more(rest.length)}</summary>
          {rest.map((it) => (
            <FacetLink key={it.key} it={it} />
          ))}
        </details>
      ) : null}
    </>
  );
}

function Facet({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="facet">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

export function AtlasFacets({
  state,
  facets,
  families,
  biotopes,
  kingdomCounts,
}: {
  state: AtlasState;
  facets: PlantFacets | null;
  families: FacetValue[] | null;
  biotopes: BiotopeFacet[] | null;
  kingdomCounts: Record<string, number | null | undefined>;
}) {
  const base: AtlasState = { ...state, page: undefined };
  const toggle = (key: FilterKey, value: string, extra: Partial<AtlasState> = {}) => {
    const active = state[key] === value;
    return { href: atlasHref(base, active ? { [key]: undefined } : { [key]: value, ...extra }), active };
  };
  const indexable = new Set(["/atlas", atlasHref({}, { kingdom: "гриб" })]);

  // Царство: только растения и грибы, счётчики по карточкам атласа с учётом остальных фильтров.
  const kingdomValues = (facets?.kingdom ?? []).map((k) => k.value).filter((v) => v === "растение" || v === "гриб");
  const kingdoms: Item[] = (kingdomValues.length ? kingdomValues : ["растение", "гриб"]).map((v) => {
    const t = toggle("kingdom", v);
    return { key: v, label: cap(KINGDOM_LABEL[v] ?? v), ...t, count: kingdomCounts[v] ?? null, follow: indexable.has(t.href) };
  });

  const toxic: Item[] = [{ key: "toxic", label: "только ядовитые", ...toggle("is_toxic", "true") }];

  const presentEdibility = new Set((facets?.edibility ?? []).map((e) => e.value));
  const edibility: Item[] = EDIBILITY_OPTIONS.filter((o) => !facets || presentEdibility.has(o.value)).map((o) => {
    const active = o.param === "edible" ? state.edible === "true" : state.edibility === o.paramValue;
    const href = atlasHref(
      base,
      active
        ? { [o.param]: undefined }
        : o.param === "edible"
          ? { edible: "true", edibility: undefined }
          : { edibility: o.paramValue, edible: undefined },
    );
    return { key: o.value, label: o.label, href, active };
  });

  // Действие: 40 самых частых канонических действий корпуса.
  const actions: Item[] = (facets?.actions ?? []).slice(0, 40).map((a) => ({ key: a.value, label: a.value, ...toggle("action", a.value) }));
  if (state.action && !actions.some((a) => a.active)) {
    actions.unshift({ key: state.action, label: state.action, href: atlasHref(base, { action: undefined }), active: true });
  }

  // Семейство: слитые написания; при выбранном царстве только его семейства.
  const activeFamily = familyKey(state.family);
  const families_: Item[] = familyFacets(families ?? [])
    .filter((f) => (state.kingdom === "гриб" ? f.fungal : state.kingdom === "растение" ? !f.fungal : true))
    .slice(0, 60)
    .map((f) => {
      const active = activeFamily === f.key;
      return {
        key: f.key,
        label: f.label,
        latin: f.latin && f.latin !== f.label ? f.latin : null,
        count: f.count,
        active,
        href: atlasHref(base, { family: active ? undefined : f.key }),
      };
    });
  if (state.family && !families_.some((f) => f.active)) {
    families_.unshift({ key: state.family, label: familyLabel(state.family), href: atlasHref(base, { family: undefined }), active: true });
  }

  const activeCount = activeFilterKeys(state).filter((k) => k !== "q").length;

  return (
    <aside className="aside atlas-aside" aria-label="Фильтры атласа">
      <input type="checkbox" id="atlas-ft" className="atlas-ft-input" />
      <label htmlFor="atlas-ft" className="atlas-ft-label btn btn-ghost btn-sm">
        Фильтры{activeCount ? `, выбрано ${activeCount}` : ""}
      </label>
      <div className="atlas-facets">
        <Facet title="Растения или грибы">
          {kingdoms.map((it) => (
            <FacetLink key={it.key} it={it} />
          ))}
        </Facet>
        <Facet title="Безопасность">
          {toxic.map((it) => (
            <FacetLink key={it.key} it={it} />
          ))}
        </Facet>
        {edibility.length ? (
          <Facet title="Съедобность">
            {edibility.map((it) => (
              <FacetLink key={it.key} it={it} />
            ))}
          </Facet>
        ) : null}
        {actions.length ? (
          <Facet title="Действие">
            <FacetList items={actions} more={(n) => `ещё ${n} действий`} />
          </Facet>
        ) : null}
        {families_.length ? (
          <Facet title="Семейство">
            <FacetList items={families_} more={(n) => `ещё ${n} семейств`} />
          </Facet>
        ) : null}
        {biotopes?.length ? (
          <Facet title="Где растёт">
            {BIOTOPE_GROUPS.map((g) => {
              const items = biotopes.filter((b) => b.group === g.group);
              if (!items.length) return null;
              return (
                <div key={g.group} className="facet-group">
                  <div className="facet-grp">{g.label}</div>
                  {items.map((b) => (
                    <FacetLink key={b.key} it={{ key: b.key, label: biotopeLabel(b.key), ...toggle("biotope", b.key) }} />
                  ))}
                </div>
              );
            })}
          </Facet>
        ) : null}
      </div>
    </aside>
  );
}

function filterLabel(k: FilterKey, v: string): string {
  switch (k) {
    case "q":
      return `«${v}»`;
    case "kingdom":
      return KINGDOM_LABEL[v] ?? v;
    case "family":
      return `семейство ${familyLabel(v)}`;
    case "action":
      return `действие «${v}»`;
    case "indication":
      return conditionPhrase(v, false);
    case "biotope":
      return `растёт ${biotopeWhere(v)}`;
    case "edible":
      return "съедобно или условно съедобно";
    case "edibility":
      return EDIBILITY_OPTIONS.find((o) => o.paramValue === v)?.label ?? `съедобность «${v}»`;
    case "is_toxic":
      return "только ядовитые";
  }
}

/** Чипы выбранных фильтров над результатами: крестик снимает один, «сбросить все» все сразу. */
export function ActiveFilters({ state }: { state: AtlasState }) {
  const keys = activeFilterKeys(state);
  if (!keys.length) return null;
  const base: AtlasState = { ...state, page: undefined };
  return (
    <div className="atlas-active" aria-label="Выбранные фильтры">
      {keys.map((k) => (
        <Link key={k} href={atlasHref(base, { [k]: undefined })} className="chip chip-leaf" rel="nofollow" title="Снять фильтр">
          {filterLabel(k, String(state[k]))} ✕
        </Link>
      ))}
      {keys.length > 1 ? (
        <Link href={atlasHref({ sort: state.sort, view: state.view })} className="chip" rel="nofollow">
          сбросить все
        </Link>
      ) : null}
    </div>
  );
}
