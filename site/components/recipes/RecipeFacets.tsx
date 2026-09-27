// Фасеты каталога рецептов: вид, что готовим, только пошаговые, книги, справка по эпохам.
// Счётчики из словаря считаются по всем домашним рецептам, а не по текущей выборке.
import Link from "next/link";
import { fmtInt } from "../../lib/api";
import {
  DOMAIN_RU, DOMAINS, ERA_RU, KIND_FACET_RU, KINDS, catalogHref,
  type CatalogState, type RecipeVocab,
} from "../../lib/api-recipes";

const ERA_ORDER = ["pre1917", "soviet", "modern", "unknown"];

function FacetLink({ href, active, label, n }: { href: string; active: boolean; label: string; n?: number | null }) {
  return (
    <Link href={href} className={active ? "active" : undefined} aria-current={active ? "true" : undefined}>
      <span>{label}</span>
      {n != null ? <span className="n">{fmtInt(n)}</span> : null}
    </Link>
  );
}

export function RecipeFacets({ vocab, state }: { vocab: RecipeVocab | null; state: CatalogState }) {
  const kindN = (k: string) => vocab?.kinds.find((x) => x.value === k)?.count ?? null;
  const domainN = (d: string) => vocab?.domains.find((x) => x.value === d)?.count ?? null;
  const cats = (vocab?.categories ?? []).filter((c) => c.value).slice(0, 30);
  if (state.category && !cats.some((c) => c.value === state.category)) {
    cats.unshift({ value: state.category, count: vocab?.categories.find((c) => c.value === state.category)?.count ?? 0 });
  }
  const eras = ERA_ORDER.map((e) => ({ e, n: vocab?.eras.find((x) => x.value === e)?.count ?? 0 })).filter((x) => x.n > 0);

  return (
    <>
      <div className="facet">
        <h4>Вид</h4>
        <FacetLink href={catalogHref(state, { kind: undefined })} active={!state.kind} label="Все" n={vocab?.total ?? null} />
        {KINDS.map((k) => (
          <FacetLink key={k} href={catalogHref(state, { kind: state.kind === k ? undefined : k })} active={state.kind === k} label={KIND_FACET_RU[k]} n={kindN(k)} />
        ))}
      </div>

      <div className="facet">
        <h4>Пошаговые</h4>
        <FacetLink
          href={catalogHref(state, { step: !state.step })}
          active={!!state.step}
          label="Только пошаговые"
          n={vocab?.step_by_step ?? null}
        />
        <p className="small muted rc-facet-note">
          Пошаговый рецепт расписывает, что и в каком порядке делать. Остальные ближе к заметке о дозе.
        </p>
      </div>

      {cats.length ? (
        <div className="facet">
          <h4>Что готовим</h4>
          {state.category ? <FacetLink href={catalogHref(state, { category: undefined })} active={false} label="Любая форма" /> : null}
          {cats.map((c) => (
            <FacetLink
              key={c.value}
              href={catalogHref(state, { category: state.category === c.value ? undefined : c.value })}
              active={state.category === c.value}
              label={c.value}
              n={c.count}
            />
          ))}
        </div>
      ) : null}

      <div className="facet">
        <h4>Книги</h4>
        <FacetLink href={catalogHref(state, { domain: undefined })} active={!state.domain} label="Все книги" />
        {DOMAINS.map((d) => (
          <FacetLink key={d} href={catalogHref(state, { domain: state.domain === d ? undefined : d })} active={state.domain === d} label={DOMAIN_RU[d]} n={domainN(d)} />
        ))}
      </div>

      {eras.length ? (
        <p className="small muted rc-eras">
          Рецепты по времени издания книг: {eras.map((x, i) => (
            <span key={x.e}>{i ? ", " : ""}{ERA_RU[x.e]} {fmtInt(x.n)}</span>
          ))}.
        </p>
      ) : null}
    </>
  );
}
