import Link from "next/link";
import { getLeaderboard, getRecentBadges } from "./lib";
import { Header, Footer, DownloadButtons, LeaderTable, FeedRow, Medallion } from "./ui";

// Живые данные (рейтинг + лента) тянутся с внутреннего backend. Без этого Next
// запекает главную СТАТИЧЕСКИ на этапе docker build, где хост `backend` недоступен,
// и в HTML навсегда уезжают пустые списки («Пока никого»). Тот же капкан, что был
// на nastoiki.pro с датами Qtickets.
export const dynamic = "force-dynamic";
export const revalidate = 0;

const PLATE = "https://botanik.fun/ui/plates";

/** Цифры корпуса — замерены на проде 2026-08-25. Это не маркетинговые «более чем»,
 *  а фактические строки в базе; при заметном росте обновлять здесь. */
const STATS = [
  ["14 000+", "растений и грибов"],
  ["43 000", "домашних рецептов"],
  ["373", "книги 1790–2020"],
  ["13 500", "готовых монографов"],
];

export default async function Home() {
  const [board, recent] = await Promise.all([getLeaderboard("global", { limit: 5 }), getRecentBadges(12)]);
  // Схлопываем ярусы одного места в одну строку: раньше «Коптево» занимало три
  // подряд идущие записи (новичок/любитель/мастер) и лента выглядела сломанной.
  const seen = new Set<string>();
  const feed = (recent?.badges ?? []).filter((b) => {
    const key = `${b.nick}·${b.place ?? b.badge_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 6);

  return (
    <>
      <Header />

      {/* ——— Герой: ясное обещание + ботанические таблицы вместо эмодзи ——— */}
      <section className="hero">
        <div className="hero-cols">
          <div>
            <span className="chip">Определитель на старинном травнике</span>
            <h1>Узнай, что растёт вокруг тебя</h1>
            <p>
              Сфотографируй растение — приложение назовёт вид и откроет о нём монограф:
              где искать, когда собирать, что из него готовили и чем оно опасно.
              Всё собрано из книг, которым до двухсот лет.
            </p>
            <div style={{ marginTop: 26 }}>
              <DownloadButtons />
            </div>
          </div>
          <div className="hero-plates">
            <img src={`${PLATE}/urtica_dioica.png`} alt="Крапива двудомная" />
            <img src={`${PLATE}/tanacetum_vulgare.png`} alt="Пижма обыкновенная" />
            <img src={`${PLATE}/chamaenerion_angustifolium.png`} alt="Иван-чай узколистный" />
          </div>
        </div>
      </section>

      {/* ——— Что внутри: наше единственное несравнимое преимущество ——— */}
      <section className="section">
        <h2 className="section-title">Не просто «определитель»</h2>
        <p className="section-lead">
          Название вида сегодня умеет подсказать кто угодно. Мы годами разбирали
          старинные травники и лечебники, чтобы за каждым названием стояло знание:
          применение, состав, время сбора, рецепты и честные предупреждения — с
          указанием книги и года.
        </p>
        <div className="cols-4">
          {STATS.map(([num, cap]) => (
            <div className="card stat" key={cap}>
              <div className="stat-num">{num}</div>
              <div className="stat-cap">{cap}</div>
            </div>
          ))}
        </div>

        <div className="card" style={{ marginTop: 18 }}>
          <div className="plant-row">
            <img src={`${PLATE}/tanacetum_vulgare.png`} alt="Пижма обыкновенная" />
            <div>
              <h3 style={{ margin: "0 0 2px", fontSize: 22 }}>Пижма обыкновенная</h3>
              <div className="plant-latin">Tanacetum vulgare</div>
              <p style={{ margin: "10px 0 0", color: "#3f4a43" }}>
                Порошки и настои соцветий издавна гнали лихорадку и глистов, а пучки
                травы клали в шкафы от моли. Растение ядовито: дозы в старых рецептах
                нельзя превышать.
              </p>
              <div>
                <span className="tag">33 рецепта</span>
                <span className="tag">эфирное масло, флавоноиды</span>
                <span className="tag tag-warn">ядовита</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ——— Как это работает ——— */}
      <section className="section">
        <h2 className="section-title">Как это работает</h2>
        <div className="cols-3">
          {[
            ["1", `${PLATE}/plantago_major.png`, "Сфотографируй",
              "Наведи камеру на растение или гриб. Ответ приходит за секунды, даже если вокруг нет связи — снимок сохранится и определится позже."],
            ["2", `${PLATE}/achillea_nobilis.png`, "Прочитай монограф",
              "Что это, чем полезно, когда собирать, чего опасаться. Каждый факт с источником: книга и год."],
            ["3", `${PLATE}/trifolium_repens.png`, "Иди на прогулку",
              "Приложение подскажет, что растёт рядом именно сейчас, соберёт маршрут вокруг тебя и отметит находки значками."],
          ].map(([n, img, t, d]) => (
            <div className="card step" key={t}>
              <img src={img} alt="" />
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                <span className="step-num">{n}</span>
                <h3 style={{ margin: 0, fontSize: 18 }}>{t}</h3>
              </div>
              <p style={{ margin: "8px 0 0", fontSize: 15, color: "#6b7368" }}>{d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ——— Живая активность: доказательство, что тут кто-то есть ——— */}
      <section className="section cols-2">
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ margin: 0, fontSize: 22 }}>Лучшие натуралисты</h2>
            <Link href="/leaderboard" className="navlink-cta" style={{ fontSize: 14 }}>весь рейтинг</Link>
          </div>
          <LeaderTable rows={board?.top ?? []} />
        </div>
        <div className="card">
          <h2 style={{ margin: "0 0 6px", fontSize: 22 }}>Свежие находки</h2>
          {feed.length ? (
            feed.map((b, i) => <FeedRow b={b} key={i} />)
          ) : (
            <p style={{ color: "#6b7368" }}>Скоро здесь появятся первые находки.</p>
          )}
        </div>
      </section>

      {/* ——— CTA ——— */}
      <section className="section card" style={{ textAlign: "center", background: "var(--leaf)", padding: "36px 24px" }}>
        <h2 style={{ margin: "4px 0 10px", fontSize: 28 }}>Пошли в лес</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 22px", maxWidth: 520 }}>
          Приложение бесплатное, без рекламы и регистрации. Поставь его перед
          выходными и узнай, мимо чего ты ходил всё это время.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>

      <Footer />
    </>
  );
}
