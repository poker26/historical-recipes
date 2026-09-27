// Текст страницы с подсвеченными фрагментами. Куски приходят из highlight.ts,
// разметка собирается из обычных элементов React, без вставки HTML строкой.
import { Fragment } from "react";
import type { Segment } from "./highlight";

/** Переводы строк оставляем вне <mark>: иначе пустая строка между абзацами цитаты
 *  рисуется жёлтой полоской. */
const LINE_BREAK = /(\s*\n\s*)/;

export function HighlightedText({ segments, titles }: { segments: Segment[]; titles?: Map<string, string> }) {
  return (
    <>
      {segments.map((s, i) => {
        if (!s.ids.length) return <Fragment key={i}>{s.text}</Fragment>;
        const title = titles
          ? Array.from(new Set(s.ids.map((id) => titles.get(id)).filter((t): t is string => !!t))).join("; ")
          : "";
        let anchorPending = s.anchor;
        return (
          <Fragment key={i}>
            {s.text.split(LINE_BREAK).map((part, j) => {
              if (!part) return null;
              if (j % 2 === 1) return <Fragment key={j}>{part}</Fragment>;
              const id = anchorPending ? "hl" : undefined;
              anchorPending = false;
              return (
                <mark key={j} id={id} className={s.strong ? "lib-hl" : undefined} title={title || undefined}>
                  {part}
                </mark>
              );
            })}
          </Fragment>
        );
      })}
    </>
  );
}
