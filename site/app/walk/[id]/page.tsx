import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer, DownloadButtons } from "../../ui";
import { Crumbs } from "../../../components/common";
import { isUuid, pluralRu } from "../../../lib/api";
import { getWalk } from "../../../lib/api-home";
import "../../places/places.css";

// Приглашение в общую прогулку: приложение шлёт ссылку /walk/{id}, страницы раньше не было.
export const dynamic = "force-dynamic";
export const revalidate = 0;

type Params = { params: { id: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const w = isUuid(params.id) ? await getWalk(params.id) : null;
  const host = w?.participants?.find((p) => p.role === "host" || p.role === "owner") ?? w?.participants?.[0];
  return {
    title: w?.place?.name ? `Прогулка: ${w.place.name}` : "Приглашение на прогулку",
    description: host?.nick ? `${host.nick} зовёт тебя на прогулку в «${w?.place?.name ?? "место"}». Открой в приложении «Что растёт» и присоединяйся.` : "Тебя зовут на прогулку в приложении «Что растёт».",
    robots: { index: false },
  };
}

const STATUS_RU: Record<string, [string, string]> = {
  planned: ["запланирована", ""],
  active: ["идёт сейчас", "live"],
  started: ["идёт сейчас", "live"],
  finished: ["завершена", "done"],
  ended: ["завершена", "done"],
  cancelled: ["отменена", "done"],
};

function whenRu(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

export default async function WalkPage({ params }: Params) {
  if (!isUuid(params.id)) notFound();
  const w = await getWalk(params.id);
  if (!w) notFound();
  const [statusRu, statusCls] = STATUS_RU[w.status] ?? [w.status, ""];
  const host = w.participants.find((p) => p.role === "host" || p.role === "owner") ?? w.participants[0];
  const n = w.participant_count ?? w.participants.length;
  const when = whenRu(w.planned_at) || whenRu(w.started_at);

  return (
    <>
      <Header active="/places" />
      <Crumbs items={[{ href: "/places", label: "Прогулки" }, { label: w.place?.name ?? "Прогулка" }]} />
      <section className="hero-grad" style={{ padding: "32px 28px" }}>
        <span className={"walk-status " + statusCls}>Прогулка {statusRu}</span>
        <h1 style={{ margin: "12px 0 6px" }}>{host?.nick ? `${host.nick} зовёт тебя` : "Тебя зовут"} {w.place?.name ? <>в «{w.place.name}»</> : "на прогулку"}</h1>
        <p className="lead" style={{ maxWidth: 620 }}>
          {when ? `Встреча ${when}. ` : ""}
          Вместе искать растения и грибы по набору места, зачитывать находки камерой и делить значок на всех.
          {n ? ` Уже ${n} ${pluralRu(n, "участник", "участника", "участников")}.` : ""}
        </p>
        {w.participants.length ? (
          <div className="people" style={{ marginTop: 14 }}>
            {w.participants.map((p, i) => (
              <Link key={i} href={p.handle ? `/p/${p.handle}` : "#"} className="person" style={{ textDecoration: "none", color: "var(--ink)" }}>
                {p.avatar ? <img src={`/avatars/${p.avatar}.png`} alt="" loading="lazy" /> : <span className="person-dot" />}
                <span>{p.nick}{p.role === "host" || p.role === "owner" ? " · ведёт" : ""}</span>
              </Link>
            ))}
          </div>
        ) : null}
        {w.place?.id ? <p style={{ marginTop: 14 }}><Link href={`/place/${w.place.id}`}>Что растёт в этом месте сейчас →</Link></p> : null}
      </section>

      <section className="section card" style={{ textAlign: "center" }}>
        <h2 style={{ margin: "4px 0 8px", fontSize: 22 }}>Присоединиться</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 16px", maxWidth: 480 }}>
          Прогулка живёт в приложении: там видно, кто что нашёл, и туда зачитываются находки.
          {w.place?.id ? " Открой место в приложении, чтобы увидеть набор видов, который вы будете искать вместе." : ""}
        </p>
        {/* Приложение разбирает chtorastet://quest/{место}/{окно}, а ссылки на прогулку пока нет. */}
        {w.place?.id ? (
          <a href={`chtorastet://quest/${w.place.id}/month-${String(new Date().getMonth() + 1).padStart(2, "0")}`} className="btn btn-primary">
            Открыть место в приложении
          </a>
        ) : null}
        <p className="footnote" style={{ marginTop: 12 }}>
          Если ничего не открылось, приложение не установлено или старой версии. Поставь его ниже и открой эту ссылку ещё раз.
        </p>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 14 }}>
          <DownloadButtons />
        </div>
      </section>
      <Footer />
    </>
  );
}
