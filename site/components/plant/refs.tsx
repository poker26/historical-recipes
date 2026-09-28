// Уход, имена, источники, родня и подвал карточки с оговоркой и приглашением в приложение.
import Link from "next/link";
import { Quote } from "../common";
import { DownloadButtons } from "../../app/ui";
import { fmtInt, plantHref, pluralRu } from "../../lib/api";
import { bookIdFor, displayName, type FieldView, type MentionBook, type SourceBook, type SpeciesCard } from "../../lib/api-plant";
import { Block, More, nBooksDat, nQuotes } from "./bits";

export function CareBlock({ card, field }: { card: SpeciesCard; field: FieldView | null }) {
  const care = (field?.care ?? []).filter((c) => c?.title && (c.voices ?? []).some((v) => v?.text));
  const sections = (field?.care_sections ?? []).map((s) => (s ?? "").trim()).filter(Boolean);
  if (!care.length && !sections.length) return null;
  return (
    <Block id="care" title="Уход" lead={field?.care_summary?.trim() || "Что книги по комнатному цветоводству пишут о свете, поливе, земле, пересадке и размножении."}>
      {care.length
        ? care.map((sec, i) => {
            const voices = (sec.voices ?? []).filter((v) => v?.text);
            const quote = (v: (typeof voices)[number], j: number) => (
              <Quote
                key={j}
                text={(v.text ?? "").trim()}
                source={v.source ? { book: v.source, bookId: bookIdFor(card.bookIds, v.source), page: v.page ?? null } : undefined}
              />
            );
            return (
              <div key={i} className="pc-care">
                <h3>{sec.title}</h3>
                {voices.slice(0, 3).map(quote)}
                {voices.length > 3 ? <More summary={`ещё ${nQuotes(voices.length - 3)}`}>{voices.slice(3, 20).map(quote)}</More> : null}
              </div>
            );
          })
        : sections.map((s, i) => (
            <p key={i} className="pc-text">
              {s}
            </p>
          ))}
    </Block>
  );
}

function MentionRow({ m }: { m: MentionBook }) {
  return (
    <li>
      {m.bookId ? <Link href={`/library/${m.bookId}`}>«{m.book}»</Link> : <span>«{m.book}»</span>}
      {m.year ? `, ${m.year}` : ""}
      {m.names.length ? <span>, в книге названо {m.names.map((n) => `«${n}»`).join(", ")}</span> : null}
      {m.pages.length ? (
        <span className="muted">
          {", стр. "}
          {m.pages.map((p, i) => (
            <span key={p}>
              {i > 0 ? ", " : null}
              {m.bookId ? <Link href={`/library/${m.bookId}/p/${p}`}>{p}</Link> : p}
            </span>
          ))}
        </span>
      ) : null}
    </li>
  );
}

export function NamesBlock({ card }: { card: SpeciesCard }) {
  if (!card.names.length && !card.mentions.length) return null;
  const chips = card.names.slice(0, 40);
  const restNames = card.names.slice(40);
  const firstBooks = card.mentions.slice(0, 12);
  const restBooks = card.mentions.slice(12);
  return (
    <Block id="names" title="Имена" lead={card.names.length ? `Старые и народные названия, под которыми ${card.kingdom === "гриб" ? "этот гриб" : "это растение"} встречается в книгах.` : undefined}>
      {chips.length ? (
        <div className="chips pc-names">
          {chips.map((n, i) => (
            <span key={i} className="chip chip-mist">{n}</span>
          ))}
        </div>
      ) : null}
      {restNames.length ? (
        <More summary={`ещё ${fmtInt(card.namesTotal - chips.length)} ${pluralRu(card.namesTotal - chips.length, "название", "названия", "названий")}`}>
          <div className="chips pc-names">
            {restNames.map((n, i) => (
              <span key={i} className="chip chip-mist">{n}</span>
            ))}
          </div>
        </More>
      ) : null}
      {firstBooks.length ? (
        <>
          <h3>Как его называли в книгах</h3>
          <ul className="pc-mentions">
            {firstBooks.map((m, i) => (
              <MentionRow key={i} m={m} />
            ))}
          </ul>
          {restBooks.length ? (
            <More summary={`ещё ${fmtInt(card.mentionBooksTotal - firstBooks.length)} ${pluralRu(card.mentionBooksTotal - firstBooks.length, "книга", "книги", "книг")}`}>
              <ul className="pc-mentions">
                {restBooks.map((m, i) => (
                  <MentionRow key={i} m={m} />
                ))}
              </ul>
              {card.mentionBooksTotal > card.mentions.length ? (
                <p className="muted small">Здесь {fmtInt(card.mentions.length)} книг из {fmtInt(card.mentionBooksTotal)}.</p>
              ) : null}
            </More>
          ) : null}
        </>
      ) : null}
    </Block>
  );
}

function SourceRow({ s }: { s: SourceBook }) {
  const counts = [
    s.quotes ? nQuotes(s.quotes) : null,
    s.compounds ? `${fmtInt(s.compounds)} ${pluralRu(s.compounds, "запись", "записи", "записей")} о составе` : null,
  ].filter(Boolean);
  return (
    <li>
      {s.bookId ? <Link href={`/library/${s.bookId}`}>{s.book}</Link> : <span>{s.book}</span>}
      {s.year ? `, ${s.year}` : ""}
      {counts.length ? <span className="muted">, {counts.join(" и ")}</span> : null}
      {s.pages.length ? (
        <div className="small muted">
          {s.pages.length === 1 ? "страница " : "страницы "}
          {s.pages.map((p, i) => (
            <span key={p}>
              {i > 0 ? ", " : null}
              {s.bookId ? <Link href={`/library/${s.bookId}/p/${p}`}>{p}</Link> : p}
            </span>
          ))}
          {s.pagesTotal > s.pages.length ? ` и ещё ${fmtInt(s.pagesTotal - s.pages.length)}` : ""}
        </div>
      ) : null}
    </li>
  );
}

export function SourcesBlock({ card }: { card: SpeciesCard }) {
  if (!card.sources.length) return null;
  const first = card.sources.slice(0, 25);
  const rest = card.sources.slice(25);
  return (
    <Block id="sources" title="Источники" lead={`Карточка собрана по ${nBooksDat(card.bookCount)}. Номер страницы открывает страницу книги, где стоит цитата.`}>
      <ol className="pc-sources">
        {first.map((s, i) => (
          <SourceRow key={i} s={s} />
        ))}
      </ol>
      {rest.length ? (
        <More summary={`ещё ${fmtInt(card.sourcesTotal - first.length)} ${pluralRu(card.sourcesTotal - first.length, "книга", "книги", "книг")}`}>
          <ol className="pc-sources" start={first.length + 1}>
            {rest.map((s, i) => (
              <SourceRow key={i} s={s} />
            ))}
          </ol>
          {card.sourcesTotal > card.sources.length ? (
            <p className="muted small">
              Здесь {fmtInt(card.sources.length)} книг из {fmtInt(card.sourcesTotal)}, в которых больше всего записей об этом виде.
            </p>
          ) : null}
        </More>
      ) : null}
    </Block>
  );
}

export function KinBlock({ card }: { card: SpeciesCard }) {
  if (!card.parent && !card.oils.length) return null;
  return (
    <Block id="kin" title="Родня">
      {card.parent ? (
        <p>
          <Link href={plantHref(card.parent.id, card.parent.latin)} className="btn btn-ghost btn-sm">
            Другие виды рода {displayName(card.parent.name)}
            {card.parent.latin ? <span className="latin">&nbsp;{card.parent.latin}</span> : null}
          </Link>
        </p>
      ) : null}
      {card.oils.length ? (
        <>
          <h3>Эфирные масла</h3>
          <div className="chips">
            {card.oils.map((o) => (
              <Link key={o.id} href={`/oils/${o.id}`} className="chip chip-leaf">
                {o.name || "Эфирное масло"}
                {o.part ? `, ${o.part}` : ""}
              </Link>
            ))}
          </div>
        </>
      ) : null}
    </Block>
  );
}

/** «по книгам 1871–2020 годов» / «по книгам 1871 года» / «по книгам». */
export function yearsLabel(min: number | null, max: number | null): string {
  if (min && max && min !== max) return `по книгам ${min}–${max} годов`;
  if (min || max) return `по книгам ${min || max} года`;
  return "по книгам";
}

export function CardFooter({ yearMin, yearMax }: { yearMin: number | null; yearMax: number | null }) {
  return (
    <>
      <p className="pc-disclaimer">Это история применения {yearsLabel(yearMin, yearMax)}, а не медицинский совет.</p>
      <section className="section card card-soft pc-invite">
        <h2>Определи растение сам</h2>
        <p>
          Сфотографируй растение или гриб на прогулке, и «Что растёт» узнает вид, откроет такую же карточку и сохранит находку
          в твоей коллекции.
        </p>
        <div className="pc-invite-btns">
          <DownloadButtons />
        </div>
      </section>
    </>
  );
}
