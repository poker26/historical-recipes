import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer, DownloadButtons } from "../ui";

export const metadata: Metadata = {
  title: "Коллекция",
  description: "Снимки, виды, значки и прогулки хранятся в приложении «Что растёт». На сайте коллекция появится позже.",
};

// Раздел второй фазы (RFC-botanik-site §5.8, §11). Пока коллекция целиком в
// приложении; страница честно говорит, как её посмотреть, и ведёт к рейтингу и
// паспортам натуралистов, которые уже открыты на сайте.
export default function CollectionPage() {
  return (
    <>
      <Header active="/collection" />
      <section className="hero-grad" style={{ padding: "40px 32px", marginTop: 16 }}>
        <span className="chip">Коллекция</span>
        <h1 style={{ margin: "12px 0 8px" }}>Твои находки живут в приложении</h1>
        <p className="lead" style={{ maxWidth: 620 }}>
          В коллекции приложения у каждой находки есть вид, дата и место. Там же твой уровень и значки.
        </p>
        <div style={{ marginTop: 22 }}>
          <DownloadButtons />
        </div>
      </section>

      <section className="section">
        <div className="card">
          <h2 style={{ fontSize: 22 }}>Рейтинг натуралистов</h2>
          <p className="muted">
            <Link href="/leaderboard">Рейтинг</Link> показывает лучших собирателей находок за всё время и за месяц.
          </p>
        </div>
      </section>
      <Footer />
    </>
  );
}
