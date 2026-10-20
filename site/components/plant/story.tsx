// «О растении»: рассказ из научно-популярных книг — родина, история, название,
// биология, применение, поверья. Каждый факт подписан книгой, годом и страницей.
import { SourceRef } from "../common";
import { pluralRu } from "../../lib/api";
import { bookIdFor, storySections, type FieldView, type SpeciesCard, type StoryFact } from "../../lib/api-plant";
import { Block, More } from "./bits";

const nFacts = (n: number) => `${n} ${pluralRu(n, "факт", "факта", "фактов")}`;

function FactView({ f, bookIds }: { f: StoryFact; bookIds: Record<string, string> }) {
  const text = (f.text ?? "").trim();
  return (
    <div className="pc-fact">
      <p className="pc-text">
        {f.about ? <b>{f.about}. </b> : null}
        {text}
      </p>
      {f.source ? (
        <SourceRef book={f.source} bookId={bookIdFor(bookIds, f.source)} year={f.year ?? null} page={f.page ?? null} />
      ) : null}
    </div>
  );
}

export function StoryBlock({ card, field }: { card: SpeciesCard; field: FieldView | null }) {
  const sections = storySections(field);
  if (!sections.length) return null;
  return (
    <Block id="story" title="О растении">
      {sections.map((s, i) => (
        <div key={i} className="pc-care">
          <h3>{s.title}</h3>
          {s.facts.slice(0, 2).map((f, j) => (
            <FactView key={j} f={f} bookIds={card.bookIds} />
          ))}
          {s.facts.length > 2 ? (
            <More summary={`ещё ${nFacts(s.facts.length - 2)}`}>
              {s.facts.slice(2).map((f, j) => (
                <FactView key={j} f={f} bookIds={card.bookIds} />
              ))}
            </More>
          ) : null}
        </div>
      ))}
    </Block>
  );
}
