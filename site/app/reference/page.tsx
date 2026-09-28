import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../ui";
import { Empty, SectionHead } from "../../components/common";
import { excerpt, fmtInt, pluralRu } from "../../lib/api";
import {
  BIOTOPE_GROUPS, BIOTOPE_GROUP_RU, biotopeHref, biotopeLabel, cap,
  getBiotopes, getCompoundVocab, getIndicationVocab, getPlantFacets, getTopOils,
  indicationsBySystem, systemLabel, type IndicationTerm,
} from "../../lib/api-reference";
import { pageMeta } from "../../components/reference/meta";
import "./reference.css";

// Словари тянутся с бэкенда при запросе и кэшируются на сутки; при сборке бэкенда нет.
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  return pageMeta({
    title: "Справочники по действиям растений, болезням, веществам и маслам",
    description:
      "Действия растений, показания по системам организма со старыми и современными названиями болезней, вещества из состава растений, эфирные масла и биотопы. Каждое понятие ведёт к растениям атласа и книгам.",
    path: "/reference",
  });
}

const TOP_PER_SYSTEM = 12;

// Сотни чипов: обычные <a> вместо Link, чтобы не раздувать данные страницы
// и не запускать предзагрузку каждой ссылки.
function IndicationChip({ i }: { i: IndicationTerm }) {
  return (
    <a href={`/indications/${i.id}`} className="chip chip-leaf" title={i.name_modern && i.name_modern !== i.name ? `по-современному ${i.name_modern}` : undefined}>
      {i.name}<span className="n">{fmtInt(i.linked_facts)}</span>
    </a>
  );
}

export default async function ReferencePage() {
  const [facets, indications, compounds, oils, biotopes] = await Promise.all([
    getPlantFacets(),
    getIndicationVocab(),
    getCompoundVocab(),
    getTopOils(24),
    getBiotopes(),
  ]);

  // Показания: верхушка каждой системы организма, полный список на /indications?system=…
  const systems = indicationsBySystem(indications);

  // Вещества: самые упоминаемые термины словаря. Иерархия словаря пока сломана
  // (флавоноиды числятся внутри эфирных масел), поэтому берём не «корни», а частоту.
  const topCompounds = [...(compounds ?? [])].sort((a, b) => b.linked_facts - a.linked_facts).slice(0, 40);

  const actions = facets?.actions ?? [];
  const bioGroups = BIOTOPE_GROUPS
    .map((g) => ({ g, items: (biotopes?.biotopes ?? []).filter((b) => b.group === g) }))
    .filter((x) => x.items.length);

  return (
    <>
      <Header active="/reference" />

      <section className="hero-grad rf-hero">
        <span className="chip">Справочники</span>
        <h1>Действия растений, болезни и вещества</h1>
        <p className="lead">
          В старых книгах болезни называют по-своему, и справочник связывает такие имена с
          современными. Водянка ведёт к отёкам, грудная жаба к стенокардии, поэтому по старому и
          по новому названию находятся одни и те же растения. Здесь же собраны действия растений,
          вещества из их состава, эфирные масла и места, где растения растут.
        </p>
        <nav className="toc" aria-label="Разделы справочника">
          <a href="#actions">Действия</a>
          <a href="#indications">Показания</a>
          <a href="#compounds">Вещества</a>
          <a href="#oils">Эфирные масла</a>
          <a href="#biotopes">Где растут</a>
        </nav>
      </section>

      <section className="section" id="actions">
        <SectionHead title="Действия растений" />
        <p className="section-lead">
          Так книги описывают, как действует растение, например мочегонное, успокаивающее или
          вяжущее. Число рядом с действием показывает, у скольких растений атласа оно упомянуто.
        </p>
        {actions.length ? (
          <div className="chips rf-chips">
            {actions.map((a) => (
              <Link key={a.value} href={`/actions/${encodeURIComponent(a.value)}`} className="chip chip-leaf">
                {a.value}<span className="n">{fmtInt(a.count)}</span>
              </Link>
            ))}
          </div>
        ) : (
          <Empty>Список действий сейчас не загрузился. Обнови страницу через минуту.</Empty>
        )}
      </section>

      <section className="section" id="indications">
        <SectionHead title="Показания по системам организма" href="/indications" more="все системы" />
        <p className="section-lead">
          От чего применяли растения, по системам организма. Число рядом с названием показывает,
          сколько записей о применении к нему относится. Если навести на название, появится
          современное, когда оно другое.
        </p>
        {systems.length ? (
          <div className="rf-systems">
            {systems.map(({ key, list }) => (
              <div key={key} className="card rf-system">
                <h3><a href={`/indications?system=${encodeURIComponent(key)}`}>{systemLabel(key)}</a></h3>
                <div className="chips rf-chips">
                  {list.slice(0, TOP_PER_SYSTEM).map((i) => <IndicationChip key={i.id} i={i} />)}
                </div>
                {list.length > TOP_PER_SYSTEM ? (
                  <a className="more" href={`/indications?system=${encodeURIComponent(key)}`}>ещё {fmtInt(list.length - TOP_PER_SYSTEM)} →</a>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <Empty>
            Словарь показаний сейчас не загрузился. Обнови страницу через минуту или открой{" "}
            <Link href="/atlas">атлас</Link>.
          </Empty>
        )}
      </section>

      <section className="section" id="compounds">
        <SectionHead title="Вещества" href="/compounds" more="весь словарь" />
        <p className="section-lead">
          Вещества из состава растений, о которых книги пишут чаще всего. Число показывает,
          сколько записей о составе растений к ним привязано.
        </p>
        {topCompounds.length ? (
          <div className="chips rf-chips">
            {topCompounds.map((c) => (
              <Link key={c.id} href={`/compounds/${c.id}`} className="chip chip-leaf">
                {c.name}<span className="n">{fmtInt(c.linked_facts)}</span>
              </Link>
            ))}
          </div>
        ) : (
          <Empty>Словарь веществ сейчас не загрузился. Обнови страницу через минуту.</Empty>
        )}
      </section>

      <section className="section" id="oils">
        <SectionHead title="Эфирные масла" href="/oils" more="все масла" />
        <p className="section-lead">
          Масла, о применении которых в книгах больше всего записей. У каждого масла есть
          растение, из которого его получают, и аромат по описанию книги.
        </p>
        {oils?.length ? (
          <div className="rf-oils">
            {oils.map((o) => (
              <Link key={o.id} href={`/oils/${o.id}`} className="card card-tight rf-oil">
                <b>{o.name}</b>
                {o.plant_name ? <span className="small muted">растение {o.plant_name}</span> : null}
                {o.aroma_profile ? <span className="small">{excerpt(o.aroma_profile, 90)}</span> : null}
                {o.uses_count ? (
                  <span className="small muted">
                    {fmtInt(o.uses_count)} {pluralRu(o.uses_count, "запись", "записи", "записей")} о применении
                  </span>
                ) : null}
              </Link>
            ))}
          </div>
        ) : (
          <Empty>Список масел сейчас не загрузился. Обнови страницу через минуту.</Empty>
        )}
      </section>

      <section className="section" id="biotopes">
        <SectionHead title="Где растут" />
        <p className="section-lead">
          Лес, луг, болото и степь это места, где книги и наблюдения находят растения. Число
          показывает, сколько видов связывают с каждым местом книги и наблюдения, считая виды
          без фотографии.
        </p>
        {bioGroups.length ? (
          <div className="rf-biotopes">
            {bioGroups.map(({ g, items }) => (
              <div key={g} className="card">
                <h3>{BIOTOPE_GROUP_RU[g] ?? cap(g)}</h3>
                <div className="chips rf-chips">
                  {items.map((b) => (
                    <Link key={b.key} href={biotopeHref(b.key)} className="chip chip-leaf">
                      {biotopeLabel(b.key)}<span className="n">{fmtInt(b.count)}</span>
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty>Список биотопов сейчас не загрузился. Обнови страницу через минуту.</Empty>
        )}
      </section>

      <p className="footnote rf-foot">
        Это история применения растений по книгам разных лет, а не медицинский совет.
      </p>

      <Footer />
    </>
  );
}
