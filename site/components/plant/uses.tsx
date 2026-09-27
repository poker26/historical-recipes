// Очерк, безопасность и применение: то, ради чего карточку открывают.
import Link from "next/link";
import { Fragment } from "react";
import { Quote, SourceRef } from "../common";
import { fmtInt, pluralRu } from "../../lib/api";
import { bookIdFor, capFirst, quoteOf, type FieldView, type SpeciesCard, type UseGroup } from "../../lib/api-plant";
import { Block, CiteView, FactList, IndicationLink, More, moreQuotesLabel, nBooksDat, nBooksGen, nQuotes } from "./bits";

function paragraphs(s: string): string[] {
  return s.split(/\n+/).map((p) => p.trim()).filter(Boolean);
}

export function EssayBlock({ card, field }: { card: SpeciesCard; field: FieldView | null }) {
  const verdict = field?.verdict?.trim() || null;
  // У комнатных описание склеено из разделов ухода: их показывает блок «Уход».
  const description = field?.origin === "houseplant" ? null : field?.description?.trim() || card.description;
  const lead = quoteOf(field?.lead_fact) ?? quoteOf(field?.fun_fact);
  if (!verdict && !description && !lead) return null;
  return (
    <Block id="essay" title="Очерк">
      {verdict ? <p className="lead pc-verdict">{verdict}</p> : null}
      {description ? (
        <div className="prose">
          {paragraphs(description).map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      ) : null}
      {lead ? (
        <Quote text={lead.text} source={lead.source ? { book: lead.source, bookId: bookIdFor(card.bookIds, lead.source), page: lead.page } : undefined} />
      ) : null}
    </Block>
  );
}

export function SafetyBlock({ card, field }: { card: SpeciesCard; field: FieldView | null }) {
  const c = field?.cautions ?? null;
  const text = c?.text?.trim() || null;
  const contra = (c?.contraindications ?? []).filter(Boolean);
  const parts = (c?.toxic_parts ?? []).filter(Boolean);
  const hasCaution = !!(text || contra.length || c?.symptoms || c?.antidote);
  if (!hasCaution && !card.toxicities.total) return null;
  return (
    <Block id="safety" title="Безопасность">
      {hasCaution ? (
        <div className="card card-tight pc-caution">
          <b>Осторожно</b>
          {text ? <p>{text}</p> : null}
          {contra.length ? <p>Противопоказания, которые называют книги: {contra.join("; ")}.</p> : null}
          {c?.symptoms ? <p>Признаки отравления: {c.symptoms}</p> : null}
          {c?.antidote ? <p>Что советовали книги: {c.antidote}</p> : null}
          {parts.length ? (
            <div className="pc-parts">
              <span className="muted">Ядовитые части:</span>
              {parts.map((p, i) => (
                <span key={i} className="chip chip-danger">{p}</span>
              ))}
            </div>
          ) : null}
          {c?.source ? <SourceRef book={c.source} bookId={bookIdFor(card.bookIds, c.source)} page={c.page ?? null} /> : null}
        </div>
      ) : null}
      {card.toxicities.total ? (
        <>
          <h3>Что пишут книги о ядовитости</h3>
          <FactList set={card.toxicities} />
        </>
      ) : null}
    </Block>
  );
}

export function IndicationsLine({ items, links = 5 }: { items: string[]; links?: number }) {
  if (!items.length) return null;
  const linked = items.slice(0, links);
  const plain = items.slice(links);
  return (
    <p className="pc-line">
      <span className="pc-label">Применяли при:</span>{" "}
      {linked.map((name, i) => (
        <span key={i}>
          {i > 0 ? ", " : null}
          <IndicationLink name={name} />
        </span>
      ))}
      {plain.length ? `${linked.length ? ", " : ""}${plain.join(", ")}` : null}
    </p>
  );
}

export function ActionLink({ action }: { action: string }) {
  return action ? (
    <Link href={`/actions/${encodeURIComponent(action)}`} className="more">растения с этим действием →</Link>
  ) : null;
}

/** Заголовок группы: одно действие текстом (ссылка рядом), несколько — каждое ссылкой. */
function GroupTitle({ g }: { g: UseGroup }) {
  if (!g.actions.length) return <>Прочее</>;
  if (g.actions.length === 1) return <>{capFirst(g.actions[0])}</>;
  return (
    <>
      {g.actions.map((a, i) => (
        <Fragment key={i}>
          {i > 0 ? ", " : null}
          <Link href={`/actions/${encodeURIComponent(a)}`} className="pc-act">{i === 0 ? capFirst(a) : a}</Link>
        </Fragment>
      ))}
    </>
  );
}

function GroupFull({ g }: { g: UseGroup }) {
  const open = g.quotes.slice(0, 2);
  const rest = g.quotes.slice(2);
  const restTotal = g.quotesTotal - open.length;
  return (
    <article className="pc-use">
      <div className="pc-use-head">
        <h3>
          <GroupTitle g={g} />
        </h3>
        <span className="muted small">
          {g.quotesTotal ? nQuotes(g.quotesTotal) : "без цитат"}
          {g.books ? ` из ${nBooksGen(g.books)}` : ""}
        </span>
        {g.actions.length === 1 ? <ActionLink action={g.actions[0]} /> : null}
      </div>
      <IndicationsLine items={g.indications} />
      {g.parts.length ? (
        <p className="pc-line">
          <span className="pc-label">Части:</span> {g.parts.join(", ")}
        </p>
      ) : null}
      {g.preparations.length ? (
        <p className="pc-line">
          <span className="pc-label">Как готовили:</span> {g.preparations.join("; ")}
        </p>
      ) : null}
      {open.map((c, i) => (
        <CiteView key={i} c={c} />
      ))}
      {rest.length ? (
        <More summary={moreQuotesLabel(restTotal, g.restBooks)}>
          {rest.map((c, i) => (
            <CiteView key={i} c={c} />
          ))}
          {restTotal > rest.length ? (
            <p className="muted small">
              Здесь {fmtInt(rest.length)} из {fmtInt(restTotal)}. Остальные цитаты найдёшь в книгах из <a href="#sources">списка источников</a>.
            </p>
          ) : null}
        </More>
      ) : null}
    </article>
  );
}

function GroupMini({ g }: { g: UseGroup }) {
  const first = g.quotes[0];
  return (
    <div className="pc-use pc-use-mini">
      <div className="pc-use-head">
        <h4>
          <GroupTitle g={g} />
        </h4>
        <span className="muted small">{g.quotesTotal ? nQuotes(g.quotesTotal) : "без цитат"}</span>
        {g.actions.length === 1 ? <ActionLink action={g.actions[0]} /> : null}
      </div>
      <IndicationsLine items={g.indications} links={0} />
      {first ? <CiteView c={first} /> : null}
    </div>
  );
}

export function UsesBlock({ card }: { card: SpeciesCard }) {
  const groups = card.useGroups;
  if (!groups.length) return null;
  const top = groups.slice(0, 12);
  const rest = groups.slice(12);
  const hidden = card.useGroupsTotal - groups.length;
  const lead = card.useBooks
    ? `Чем лечили, при каких болезнях и какой частью, по ${nBooksDat(card.useBooks)}. Сначала идут действия, о которых книги пишут чаще.`
    : "Записи книг о том, чем лечили, при каких болезнях и какой частью.";
  return (
    <Block id="uses" title="Применение" lead={lead}>
      {top.map((g, i) => (
        <GroupFull key={i} g={g} />
      ))}
      {rest.length ? (
        <More className="pc-more-groups" summary={`ещё ${fmtInt(rest.length + hidden)} ${pluralRu(rest.length + hidden, "действие", "действия", "действий")}`}>
          <div className="pc-mini-grid">
            {rest.map((g, i) => (
              <GroupMini key={i} g={g} />
            ))}
          </div>
          {hidden > 0 ? (
            <p className="muted small">
              И ещё {fmtInt(hidden)} {pluralRu(hidden, "действие", "действия", "действий")}, о которых книги пишут реже всего.
            </p>
          ) : null}
        </More>
      ) : null}
    </Block>
  );
}
