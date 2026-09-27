import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer, DownloadButtons } from "../ui";

export const metadata: Metadata = {
  title: "Коллекция",
  description: "Коллекция находок живёт в приложении «Что растёт»: снимки, виды, значки и прогулки. На сайте она появится следующим этапом.",
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
          Каждый снимок, который ты определил в «Что растёт», сохраняется в коллекцию
          вместе с видом, датой и местом. Открой вкладку «Коллекция» в приложении,
          чтобы посмотреть свой гербарий, уровень и значки.
        </p>
        <div style={{ marginTop: 22 }}>
          <DownloadButtons />
        </div>
      </section>

      <section className="section cols-2">
        <div className="card">
          <h2 style={{ fontSize: 22 }}>Что уже есть на сайте</h2>
          <p className="muted">
            Публичный паспорт натуралиста с уровнем и значками открывается по ссылке из
            приложения, а <Link href="/leaderboard">рейтинг</Link> показывает лучших собирателей
            находок за всё время и за сезон.
          </p>
        </div>
        <div className="card">
          <h2 style={{ fontSize: 22 }}>Что появится дальше</h2>
          <p className="muted">
            Коллекция на большом экране: сетка, карта без точных координат и календарь
            находок, заметки и полки, экспорт архива со снимками. Для этого телефон
            привяжется к аккаунту на сайте одним кодом, без пароля.
          </p>
        </div>
      </section>
      <Footer />
    </>
  );
}
