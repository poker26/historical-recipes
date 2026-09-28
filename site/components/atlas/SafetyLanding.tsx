// Подборки атласа по шкале съедобности: съедобные и ядовитые грибы, съедобные и ядовитые
// растения. У каждой свой адрес, потому что это самостоятельные запросы («ядовитые грибы»)
// со своим заголовком и пояснением, а не комбинация фильтров атласа.
// Шкала (backend/app/services/edible_safety.py): 1 съедобно, 2 условно съедобно, только
// после обработки, 3 опасно, как еду не употребляют, 4 смертельно ядовито.
import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../../app/ui";
import { Crumbs, Empty, Pager, Safety } from "../common";
import { DEFAULT_OG, SITE_URL, fmtInt, pluralRu } from "../../lib/api";
import { listPlants } from "../../lib/api-atlas";
import { PhotoSourcesNote, PlantGrid } from "./plants";

const PAGE_SIZE = 48;

export type SafetyLanding = {
  path: string;
  kingdom: "гриб" | "растение";
  min: number;
  max: number;
  title: string;
  /** Начало описания для поисковика, после числа видов: «съедобных и условно съедобных грибов». */
  what: string;
  lead: string;
  caution: { level: "danger" | "warn"; title: string; text: string };
};

export const SAFETY_LANDINGS: SafetyLanding[] = [
  {
    path: "/atlas/edible-mushrooms",
    kingdom: "гриб",
    min: 1,
    max: 2,
    title: "Съедобные грибы",
    what: "съедобных и условно съедобных грибов",
    lead:
      "Грибы, которые по книгам едят. Среди них есть условно съедобные: их едят только после вымачивания или отваривания, это сказано в карточке гриба.",
    caution: {
      level: "warn",
      title: "У многих съедобных грибов есть ядовитые двойники.",
      text: "Двойник назван в карточке гриба. Собирай только те грибы, которые знаешь наверняка.",
    },
  },
  {
    path: "/atlas/poisonous-mushrooms",
    kingdom: "гриб",
    min: 3,
    max: 4,
    title: "Ядовитые грибы",
    what: "ядовитых грибов",
    lead: "Грибы, которыми по книгам и справочникам можно отравиться, в том числе смертельно ядовитые.",
    caution: {
      level: "danger",
      title: "Эти грибы не едят.",
      text: "Некоторые из них похожи на съедобные, признаки отличия указаны в карточке гриба.",
    },
  },
  {
    path: "/atlas/edible-plants",
    kingdom: "растение",
    min: 1,
    max: 2,
    title: "Съедобные растения",
    what: "съедобных растений",
    lead:
      "Растения, которые по книгам едят: дикие травы, ягоды, коренья и огородные культуры. У условно съедобных в карточке сказано, какую часть едят и как её готовят.",
    caution: {
      level: "warn",
      title: "Съедобной бывает только часть растения.",
      text: "Какие части едят и с каким растением его легко спутать, сказано в карточке.",
    },
  },
  {
    path: "/atlas/poisonous-plants",
    kingdom: "растение",
    min: 3,
    max: 4,
    title: "Ядовитые растения",
    what: "ядовитых растений",
    lead:
      "Растения, которыми можно отравиться. Многие из них травники применяли как лекарство в малых дозах, но как еду их не употребляют.",
    caution: {
      level: "danger",
      title: "Эти растения опасны.",
      text: "Какие части ядовиты и как проявляется отравление, сказано в карточке растения.",
    },
  },
];

export const safetyLanding = (path: string): SafetyLanding => SAFETY_LANDINGS.find((l) => l.path === path)!;

function pageOf(raw: string | string[] | undefined): number {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v && /^\d{1,4}$/.test(v) && Number(v) >= 1 ? Number(v) : 1;
}

const query = (l: SafetyLanding) => ({
  kingdom: l.kingdom,
  safety_min: l.min,
  safety_max: l.max,
  published: true,
  sort: "uses" as const,
});

export async function safetyLandingMeta(l: SafetyLanding, pageRaw?: string | string[]): Promise<Metadata> {
  const page = pageOf(pageRaw);
  const n = (await listPlants({ ...query(l), limit: 1 })).total;
  const description = n
    ? `${fmtInt(n)} ${l.what} с фотографиями. ${l.lead}`
    : l.lead;
  const url = SITE_URL + l.path;
  return {
    title: l.title,
    description,
    alternates: { canonical: url },
    openGraph: { title: l.title, description, url, type: "website", images: [DEFAULT_OG] },
    // Первая страница подборки в индексе; следующие открываются по ссылкам, а сами виды
    // и так есть в карте сайта.
    robots: n && page === 1 ? undefined : { index: false, follow: true },
  };
}

export async function SafetyLandingPage({ l, pageRaw }: { l: SafetyLanding; pageRaw?: string | string[] }) {
  const page = pageOf(pageRaw);
  const list = await listPlants({ ...query(l), limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE });
  const pages = Math.max(1, Math.ceil(list.total / PAGE_SIZE));
  const others = SAFETY_LANDINGS.filter((x) => x.path !== l.path);

  return (
    <>
      <Header active="/atlas" />
      <Crumbs items={[{ href: "/atlas", label: "Атлас" }, { label: l.title }]} />
      <section className="hero-grad atlas-hero atlas-hero-compact">
        <h1>{l.title}</h1>
        <p className="lead">
          {list.total ? `В атласе ${fmtInt(list.total)} ${pluralRu(list.total, "вид", "вида", "видов")} с фотографией. ` : ""}
          {l.lead}
        </p>
      </section>

      <Safety level={l.caution.level} title={l.caution.title} text={l.caution.text} />

      <section className="section">
        {!list.ok ? (
          <Empty>
            Атлас сейчас не отвечает. Обнови страницу через минуту или открой <Link href="/atlas">атлас</Link>.
          </Empty>
        ) : list.items.length ? (
          <PlantGrid items={list.items} />
        ) : (
          <Empty>
            Такой страницы в подборке нет. <Link href={l.path}>Вернуться к первой</Link>
          </Empty>
        )}
        <Pager page={page} pages={pages} base={l.path} />
      </section>

      <section className="section">
        <h2>Другие подборки атласа</h2>
        <div className="chips">
          {others.map((o) => (
            <Link key={o.path} href={o.path} className="chip">
              {o.title}
            </Link>
          ))}
          <Link href="/atlas?kingdom=%D0%B3%D1%80%D0%B8%D0%B1" className="chip">
            Все грибы атласа
          </Link>
        </div>
      </section>

      <PhotoSourcesNote />
      <p className="footnote">
        Съедобность и ядовитость собраны по старым книгам и современным справочникам. Это справка, а не разрешение
        собирать и есть растение или гриб.
      </p>
      <Footer />
    </>
  );
}
