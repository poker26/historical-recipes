import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../ui";
import { Crumbs, MONTHS_PREP_RU, MONTHS_RU } from "../../components/common";
import { DEFAULT_OG, SITE_URL } from "../../lib/api";
import { seasonHref } from "../../lib/season";
import "./season.css";

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

const title = "Что собирать по месяцам";
const description =
  "Календарь сбора растений по старым травникам и справочникам. Для каждого месяца собраны растения, которые в нём заготавливают, с частью растения, сроком и способом сушки.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: `${SITE_URL}/season` },
  openGraph: { title, description, url: `${SITE_URL}/season`, type: "website", images: [DEFAULT_OG] },
};

export default function SeasonIndex() {
  const now = new Date().getMonth() + 1;
  return (
    <>
      <Header active="/atlas" />
      <Crumbs items={[{ href: "/atlas", label: "Атлас" }, { label: title }]} />
      <section className="hero-grad atlas-hero atlas-hero-compact">
        <h1>{title}</h1>
        <p className="lead">
          Выбери месяц, и откроется список растений, которые в нём заготавливают по книгам, от травников XIX века до
          справочников второй половины XX.
        </p>
      </section>
      <section className="section">
        <ul className="season-months">
          {MONTHS_RU.map((name, i) => (
            <li key={name}>
              <Link href={seasonHref(i + 1)} className={"card season-month" + (i + 1 === now ? " is-now" : "")}>
                <b>{cap(name)}</b>
                <span className="small muted">Что собирать в {MONTHS_PREP_RU[i]}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <Footer />
    </>
  );
}
