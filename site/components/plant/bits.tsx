// Мелкие кирпичи карточки вида: блок с якорем, цитата, «ещё N» под спойлером,
// список записей (сбор, места, еда, ядовитость) и перевод уровня безопасности в плашку.
import Link from "next/link";
import type { ReactNode } from "react";
import { Quote, SourceRef, type SafetyLevel } from "../common";
import { fmtInt, pluralRu } from "../../lib/api";
import { ruQuotes, type Cite, type Fact, type FactSet, type SafetyInfo } from "../../lib/api-plant";

export function Block({ id, title, lead, children }: { id: string; title: string; lead?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="block pc-block">
      <h2>{title}</h2>
      {lead ? <p className="pc-lead">{lead}</p> : null}
      {children}
    </section>
  );
}

/** Цитата с книгой, годом и страницей; без текста остаётся строка источника. */
export function CiteView({ c }: { c: Cite }) {
  const source = { book: c.book, bookId: c.bookId, year: c.year, page: c.page };
  if (c.text) return <Quote text={c.text} source={source} />;
  return c.book || c.bookId ? <div className="pc-src-only"><SourceRef {...source} /></div> : null;
}

export function More({ summary, children, className }: { summary: string; children: ReactNode; className?: string }) {
  return (
    <details className={"pc-more" + (className ? " " + className : "")}>
      <summary>{summary}</summary>
      <div className="pc-more-body">{children}</div>
    </details>
  );
}

export const nQuotes = (n: number) => `${fmtInt(n)} ${pluralRu(n, "цитата", "цитаты", "цитат")}`;
export const nBooksGen = (n: number) => `${fmtInt(n)} ${pluralRu(n, "книги", "книг", "книг")}`;
export const nBooksDat = (n: number) => `${fmtInt(n)} ${pluralRu(n, "книге", "книгам", "книгам")}`;
export const nRecords = (n: number) => `${fmtInt(n)} ${pluralRu(n, "запись", "записи", "записей")}`;

/** «ещё 12 цитат из 5 книг» для спойлера. */
export function moreQuotesLabel(n: number, books: number): string {
  return `ещё ${nQuotes(n)}` + (books > 0 ? ` из ${nBooksGen(books)}` : "");
}

function FactRow({ f }: { f: Fact }) {
  return (
    <div className="pc-fact">
      {f.meta.length ? (
        <dl className="pc-fact-meta">
          {f.meta.map((m, i) => (
            <div key={i}>
              <dt>{m.label}</dt>
              <dd>{m.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <CiteView c={f.cite} />
    </div>
  );
}

/** Записи блока: первые `open` видны, остальные под «ещё», с честным хвостом. */
export function FactList({ set, open = 3 }: { set: FactSet; open?: number }) {
  const first = set.items.slice(0, open);
  const rest = set.items.slice(open);
  const restTotal = set.total - first.length;
  return (
    <div className="pc-facts">
      {first.map((f, i) => (
        <FactRow key={i} f={f} />
      ))}
      {rest.length ? (
        <More summary={`ещё ${nRecords(restTotal)}`}>
          {rest.map((f, i) => (
            <FactRow key={i} f={f} />
          ))}
          {set.total > set.items.length ? (
            <p className="muted small">
              Здесь первые {fmtInt(set.items.length)} из {fmtInt(set.total)}. Остальные записи найдёшь в книгах из <a href="#sources">списка источников</a>.
            </p>
          ) : null}
        </More>
      ) : null}
    </div>
  );
}

/** Уровень безопасности → плашка по правилу сайта. Молчание книг не превращаем в «безопасно». */
export function safetyView(s: SafetyInfo | null | undefined, isToxic: boolean): { level: SafetyLevel; title: string } {
  const lvl = s?.level;
  if (lvl === 4) return { level: "danger", title: "Смертельно ядовито" };
  if (lvl === 3) return { level: "warn", title: "Лекарственное и ядовитое, опасно в больших дозах" };
  if (lvl === 2) return { level: "warn", title: "Условно съедобно" };
  if (lvl === 1) return { level: "ok", title: "Съедобно" };
  if ((lvl === null || lvl === undefined) && isToxic) return { level: "danger", title: "Ядовитое растение" };
  return { level: "unknown", title: "Съедобность не подтверждена" };
}

/** Что сказать при уровне 0, когда у уровня нет своего обоснования. */
const UNKNOWN_TEXT = "Книги ничего не говорят о том, можно ли его есть. Не ешь его, пока надёжный источник этого не подтвердит.";

/** Обоснование уровня. Служебная пометка «[auto] …» — это не слова книг, её не показываем. */
export function safetyText(s: SafetyInfo | null | undefined): string | null {
  // Служебная метка «[anchor:deadly]» говорит, что уровень 4 поставлен по списку
  // смертельных родов. Читателю она не нужна, обоснование остаётся.
  const r = (s?.rationale ?? "").replace(/^\[anchor:[a-z]+\]\s*/i, "").trim();
  const unknown = !s || s.level == null || s.level === 0;
  if (!r || r.startsWith("[auto]")) {
    if (unknown) return UNKNOWN_TEXT;
    return r ? "Уровень посчитан автоматически по записям книг, человек его ещё не проверял." : null;
  }
  return r;
}

/** Ссылка на атлас по показанию. */
export function IndicationLink({ name }: { name: string }) {
  return (
    <Link href={`/atlas/for/${encodeURIComponent(name.toLowerCase())}`} className="pc-ind">
      {ruQuotes(name)}
    </Link>
  );
}
