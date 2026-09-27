// Переход между страницами книги: соседние, первая, последняя и «к странице №».
// Форма обычная GET: страница источника ловит ?go= и перенаправляет на нужный номер.
import Link from "next/link";
import type { BookPage } from "../../lib/api-library";

export function PageNav({ bookId, n, nav }: { bookId: string; n: number; nav: BookPage["nav"] }) {
  const href = (p: number) => `/library/${bookId}/p/${p}`;
  return (
    <nav className="pagenav lib-pagenav" aria-label="Страницы книги">
      {nav.prev ? (
        <Link href={href(nav.prev)} className="btn btn-ghost btn-sm" rel="prev">← стр. {nav.prev}</Link>
      ) : (
        <span className="btn btn-sm btn-disabled">начало книги</span>
      )}
      <div className="lib-pagenav-mid">
        {nav.first && nav.first !== n ? <Link href={href(nav.first)}>первая</Link> : <span className="muted">первая</span>}
        {nav.last && nav.last !== n ? <Link href={href(nav.last)}>последняя, {nav.last}</Link> : <span className="muted">последняя</span>}
        <form action={href(n)} method="get" className="lib-go">
          <label>
            к странице №
            <input type="number" name="go" min={nav.first ?? 1} max={nav.last ?? undefined} inputMode="numeric" required />
          </label>
          <button type="submit" className="btn btn-ghost btn-sm">Открыть</button>
        </form>
      </div>
      {nav.next ? (
        <Link href={href(nav.next)} className="btn btn-ghost btn-sm" rel="next">стр. {nav.next} →</Link>
      ) : (
        <span className="btn btn-sm btn-disabled">конец книги</span>
      )}
    </nav>
  );
}
