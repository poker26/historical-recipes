import Chat from "./Chat";
import { QTICKETS_URL, CHANNEL_URL, MAX_CHANNEL_URL, TG_PERSONAL, MAX_PHONE, GIFT_BOOK_URL, MAX_BOT_URL, qt } from "./lib";
import TrackedLink from "./Tracked";
import { Alambic, Sprig, Wormwood, Divider, Corner } from "./ornaments";
import { getMasterclassDates } from "./qtickets";

// Render at runtime so the Qtickets token (server env, not present at build time)
// is available; the Qtickets fetch itself is cached ~30 min (see qtickets.ts).
export const dynamic = "force-dynamic";

const SITE = "https://nastoiki.pro";

// FAQ — feeds both the visible section (indexable, keyword-rich text) and the
// FAQPage structured data below.
const FAQ: { q: string; a: string; link?: { url: string; label: string } }[] = [
  {
    q: "Что такое настойка (тинктура) и чем она отличается от магазинной?",
    a: "Тинктура — это концентрированный настой трав, кореньев и специй на ржаном дистилляте, приготовленный по определённым пропорциям и с экстракцией дистиллятом определённой крепости в зависимости от исходного сырья. Естественно, в отличие от магазинных настоек на эссенциях, в наших тинктурах нет ароматизаторов. Те травы и коренья, которые растут у нас, — собраны мной в ближайших лесах и полях, а не куплены на маркетплейсах.",
  },
  {
    q: "По каким рецептам вы делаете настойки?",
    a: "Мы работаем по дореволюционным книгам конца XVIII — начала XIX века: «Полный винокуръ и дистиллятор» 1802 года, Брейтенбах 1803, руководства Альмедингена и Штриттера и другие. Старинные названия трав расшифровываем по Ботаническому словарю Анненкова 1878 года.",
  },
  {
    q: "Где и как проходят мастер-классы по настойкам?",
    a: "Мастер-классы проходят в алхимической лаборатории в деревне Пронино под Серпуховом (Московская область), воссозданной по книгам XIX века. Вы своими руками готовите настойку из настоящих трав и кореньев на хлебном дистилляте и дегустируете из рюмок старинного стекла.",
  },
  {
    q: "Сколько стоит мастер-класс и как записаться?",
    a: "Стоимость — от 5000 ₽. Записаться и выбрать удобную дату можно по ссылке на страницу продажи билетов; ближайшие даты показаны прямо на этом сайте.",
  },
  {
    q: "Можно ли собирать травы для настоек самому?",
    a: "Да, и это лучший вариант: полынь, дягиль, зверобой, калган и многие другие растения растут в средней полосе. Важно знать, когда и что собирать, и уметь отличать растения от ядовитых двойников (например, дягиль от борщевика). Обо всём этом расскажет наш мастер настоек в чате. А для определения растений «в поле» рекомендую пользоваться моим приложением — ",
    link: { url: "https://botanik.fun", label: "botanik.fun" },
  },
];

export default async function Home() {
  const mk = await getMasterclassDates();

  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      { "@type": "WebSite", name: "Настойки.pro", url: SITE, inLanguage: "ru" },
      {
        "@type": "LocalBusiness",
        "@id": `${SITE}/#business`,
        name: "Настойки.pro — Корни, Травы, Дистиллят",
        description:
          "Старинные русские настойки, наливки и бальзамы по дореволюционным рецептам и мастер-классы по их приготовлению.",
        url: SITE,
        image: `${SITE}/img/og.jpg`,
        priceRange: "от 5000 ₽",
        sameAs: [CHANNEL_URL, QTICKETS_URL],
        address: {
          "@type": "PostalAddress",
          addressCountry: "RU",
          addressRegion: "Московская область",
          addressLocality: "деревня Пронино, городской округ Серпухов",
        },
      },
      ...(mk
        ? mk.dates.map((d) => ({
            "@type": "Event",
            name: mk.name,
            startDate: d.iso,
            eventAttendanceMode: "https://schema.org/OfflineEventAttendanceMode",
            eventStatus: "https://schema.org/EventScheduled",
            image: `${SITE}/img/lab.jpg`,
            location: {
              "@type": "Place",
              name: "Алхимическая лаборатория в Пронино",
              address: mk.place,
            },
            organizer: { "@type": "Organization", name: "Корни, Травы, Дистиллят", url: SITE },
            offers: {
              "@type": "Offer",
              price: "5000",
              priceCurrency: "RUB",
              url: mk.buyUrl,
              availability: "https://schema.org/InStock",
            },
          }))
        : []),
      {
        "@type": "FAQPage",
        mainEntity: FAQ.map((f) => ({
          "@type": "Question",
          name: f.q,
          acceptedAnswer: { "@type": "Answer", text: f.a + (f.link ? f.link.url : "") },
        })),
      },
    ],
  };

  return (
    <main>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      {/* ─────────────  HERO  ───────────── */}
      <header className="hero has-photo">
        <div className="hero-photo" style={{ backgroundImage: "url(/img/hero.jpg)" }} />
        <Sprig className="hero-art sprig-l" />
        <Wormwood className="hero-art sprig-r" flip />
        <Alambic className="hero-art still" />
        <div className="hero-inner">
          <span className="eyebrow">Корни · Травы · Дистиллят</span>
          <h1>Искусство<br /><em>никуда не спешить</em></h1>
          <p className="lede">
            Старинные русские настойки и бальзамы по книгам двухсотлетней давности — из
            времён, когда суеты было меньше, времени больше, а слова «эффективность» ещё
            не придумали. Расспросите мастера обо всём и приезжайте сделать свою тинктуру
            своими руками.
          </p>
          <div className="hero-cta">
            <a href="#master" className="btn btn-gold">✦ Спросить мастера</a>
            <TrackedLink href={qt(QTICKETS_URL, "hero")} goal="buy_click" className="btn btn-outline">
              На мастер-класс
            </TrackedLink>
          </div>
        </div>
        <div className="hero-scroll">Листайте<span /></div>
      </header>

      {/* ─────────────  CRAFT  ───────────── */}
      <section className="section craft">
        <div className="wrap">
          <Divider className="divider" />
          <h2>Забытое искусство тинктур</h2>
          <p className="kicker">
            Мы работаем по книгам конца XVIII — начала XIX века: расшифровываем полустёртые
            названия трав, взвешиваем в золотниках и лотах, вытягиваем из корня всю его силу.
            Никаких эссенций и «залил и забыл» — только настоящая, медленная экстракция.
          </p>
          <p className="manifest">«Современные настойки — фастфуд. Тинктуры — медленная магия».</p>
        </div>
      </section>

      {/* ─────────────  ЗНАТОК (chat)  ───────────── */}
      <section id="master" className="section talk">
        <div className="wrap">
          <div className="head">
            <span className="eyebrow">Живой разговор</span>
            <h2>Спросите мастера настоек</h2>
            <p>
              Он знает рецепты из старинных книг, объяснит, зачем в настойке каждый корешок,
              с чем его сочетать и когда собирать. Спросите про любой напиток или траву.
            </p>
          </div>
          <div className="talk-frame">
            <Corner className="corner tl" />
            <Corner className="corner tr" />
            <Corner className="corner bl" />
            <Corner className="corner br" />
            <Chat />
          </div>
        </div>
      </section>

      {/* ─────────────  ПОДАРОК (lead magnet)  ───────────── */}
      <section className="section gift">
        <div className="wrap">
          <div className="gift-inner">
            <div className="gift-icon">📖</div>
            <div>
              <span className="eyebrow on-dark">Подарок</span>
              <h2>Заберите старинную книгу рецептов</h2>
              <p>
                Дарим подлинное издание 1858 года — «50 малороссийских способов настаивать».
                Настоящие дореволюционные рецепты из нашей библиотеки, бесплатно. Наш мастер
                пришлёт её вам в мессенджере — а заодно ответит на любой вопрос про настойки.
              </p>
              <div className="gift-cta">
                <TrackedLink href={GIFT_BOOK_URL} goal="gift_click" className="btn btn-gold">
                  🎁 Получить в Telegram
                </TrackedLink>
                <TrackedLink href={MAX_BOT_URL} goal="gift_click_max" className="btn btn-outline">
                  Получить в Max
                </TrackedLink>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ─────────────  АТМОСФЕРА (gallery)  ───────────── */}
      <section className="section gallery">
        <div className="wrap">
          <div className="head">
            <span className="eyebrow">Как это бывает</span>
            <h2>Настоящие кадры из лаборатории</h2>
            <p>
              Это не картинки из интернета — это наши мастер-классы в Пронино:
              коллекция тинктур, старинное стекло, дореволюционные книги и настойки,
              которые гости делают своими руками.
            </p>
          </div>
          <div className="gallery-grid">
            <figure className="gtile">
              <img src="/img/real/tinctures.jpg" alt="Коллекция тинктур с рукописными этикетками" loading="lazy" />
              <figcaption>
                <b>Коллекция тинктур</b>
                <span>Больше сотни настоев трав и кореньев с рукописными этикетками — палитра для сложных рецептов.</span>
              </figcaption>
            </figure>
            <figure className="gtile">
              <img src="/img/real/process.jpg" alt="Разбор рецепта по старинной книге на мастер-классе" loading="lazy" />
              <figcaption>
                <b>Живой мастер-класс</b>
                <span>Разбираем рецепт по дореволюционной книге и составляем свою настойку из настоящих трав.</span>
              </figcaption>
            </figure>
            <figure className="gtile">
              <img src="/img/real/glass.jpg" alt="Гранёная антикварная рюмка" loading="lazy" />
              <figcaption>
                <b>Старинное стекло</b>
                <span>Гранёные рюмки и графины XIX века из личной коллекции — из них и полагается дегустировать.</span>
              </figcaption>
            </figure>
            <figure className="gtile">
              <img src="/img/real/book1803.jpg" alt="Книга «Полный винокуръ и дистиллаторъ» 1803 года" loading="lazy" />
              <figcaption>
                <b>По книгам 1803 года</b>
                <span>«Полный винокуръ и дистиллаторъ» и другие подлинные издания — наши рабочие источники.</span>
              </figcaption>
            </figure>
            <figure className="gtile">
              <img src="/img/real/bottles.jpg" alt="Готовые настойки в бутылочках" loading="lazy" />
              <figcaption>
                <b>Своя настойка — с собой</b>
                <span>То, что вы заберёте домой: собственная настойка, сделанная руками от корня до капли.</span>
              </figcaption>
            </figure>
            <figure className="gtile">
              <img src="/img/real/team.jpg" alt="Участники мастер-класса в Пронино" loading="lazy" />
              <figcaption>
                <b>В хорошей компании</b>
                <span>Неспешно, с историями и дегустацией — так проходит вечер в алхимической лаборатории.</span>
              </figcaption>
            </figure>
          </div>
        </div>
      </section>

      {/* ─────────────  КАК ПРОХОДИТ  ───────────── */}
      <section className="section howto">
        <div className="wrap">
          <div className="head">
            <span className="eyebrow">Мастер-класс</span>
            <h2>Как проходит занятие</h2>
          </div>
          <div className="howto-facts">
            <div className="howto-fact"><b>3 часа</b><span>длительность</span></div>
            <div className="howto-fact"><b>до 6 человек</b><span>камерная группа</span></div>
            <div className="howto-fact"><b>своё — с собой</b><span>забираете настойки</span></div>
          </div>
          <div className="howto-parts">
            <div className="howto-part">
              <span className="num">1</span>
              <h3>Теоретическая часть</h3>
              <p>
                Начинаем с истории и теории. Разбираем, как готовится основа — ржаной
                дистиллят, методологию настаивания и заготовки нужных ингредиентов. Говорим
                о химии процесса, о том, как травы и коренья сочетаются между собой и какими
                лечебными свойствами обладают. Отдельно я рассказываю, из чего, когда и в
                каких количествах пили настойки в старину, и показываю, в какие бутылки их
                разливали.
              </p>
            </div>
            <div className="howto-part">
              <span className="num">2</span>
              <h3>Практическая часть</h3>
              <p>
                Затем — за дело. Каждый делает несколько настоек из готовых тинктур: добавляем
                нужное число капель в ржаную основу. Сначала работаем по книге Альмедингена
                1898 года — она попроще, — а потом, по желанию, берёмся за более сложные и
                старые издания 1796–1803 годов. Всё, что вы приготовите, забираете с собой.
              </p>
            </div>
          </div>
          <p className="howto-note">
            По отдельной договорённости я встречу вашу группу на машине у железнодорожного
            вокзала в Чехове и отвезу обратно — тогда можно будет не только готовить, но и
            дегустировать прямо на месте.
          </p>
        </div>
      </section>

      {/* ─────────────  МАСТЕР-КЛАССЫ  ───────────── */}
      <section className="section mk">
        <div className="wrap">
          <div className="mk-grid">
            <div>
              <span className="eyebrow on-dark">Алхимическая лаборатория в Пронино</span>
              <h2>Сделайте настойку своими руками</h2>
              <p>
                Из настоящих трав и кореньев, на хлебном дистилляте, из рюмок старинного
                стекла — под Серпуховом, в лаборатории, воссозданной по книгам XIX века.
              </p>
              <div className="price">Мастер-классы по тинктурам — от 5000 ₽</div>
              {mk && (
                <div className="mk-dates">
                  <div className="mk-dates-label">Ближайшие даты — выберите на странице записи:</div>
                  <div className="mk-dates-row">
                    {mk.dates.map((d) => (
                      <TrackedLink key={d.iso} href={qt(mk.buyUrl, "date_chip")} goal="buy_click" className="mk-date">
                        <b>{d.day}</b>
                        <span>{d.weekday}</span>
                      </TrackedLink>
                    ))}
                  </div>
                </div>
              )}
              <TrackedLink href={qt(mk?.buyUrl || QTICKETS_URL, "mk_cta")} goal="buy_click" className="btn btn-gold">
                🎟 Записаться на мастер-класс
              </TrackedLink>
            </div>
            <div className="mk-photo">
              <img src="/img/lab.jpg" alt="Медный перегонный куб в лаборатории Пронино" loading="lazy" />
            </div>
          </div>
        </div>
      </section>

      {/* ─────────────  FAQ  ───────────── */}
      <section className="section faq">
        <div className="wrap">
          <div className="head">
            <span className="eyebrow">Частые вопросы</span>
            <h2>О настойках и мастер-классах</h2>
          </div>
          <div className="faq-list">
            {FAQ.map((f, i) => (
              <details key={i} className="faq-item">
                <summary>{f.q}</summary>
                <p>
                  {f.a}
                  {f.link ? (
                    <a href={f.link.url} target="_blank" rel="noopener noreferrer">{f.link.label}</a>
                  ) : null}
                </p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* ─────────────  КОНТАКТЫ  ───────────── */}
      <section className="section contacts">
        <div className="wrap">
          <div className="head">
            <span className="eyebrow">Связаться</span>
            <h2>Пишите — отвечу лично</h2>
          </div>
          <div className="contact-grid">
            <a className="contact-card" href={TG_PERSONAL} target="_blank" rel="noopener noreferrer">
              <IcTelegram className="ic" />
              <b>Telegram</b>
              <span>@hippo26 — вопросы, брони, сотрудничество</span>
            </a>
            <div className="contact-card">
              <IcMax className="ic" />
              <b>Max</b>
              <span>{MAX_PHONE} — найдите меня по номеру</span>
            </div>
            <a className="contact-card" href={CHANNEL_URL} target="_blank" rel="noopener noreferrer">
              <IcTelegram className="ic" />
              <b>Канал в Telegram</b>
              <span>«Корни, Травы, Дистиллят» — рецепты и истории</span>
            </a>
            <a className="contact-card" href={MAX_CHANNEL_URL} target="_blank" rel="noopener noreferrer">
              <IcMax className="ic" />
              <b>Канал в Max</b>
              <span>Те же корни и травы — для тех, кто в Max</span>
            </a>
          </div>
        </div>
      </section>

      {/* ─────────────  FINEPRINT + FOOTER  ───────────── */}
      <section className="fineprint">
        <div className="wrap">
          <p className="disclaimer">
            Материалы сайта и мастер-классов носят историко-культурный характер и не являются
            медицинским советом или призывом к неконтролируемому употреблению алкоголя.
            Естественно, поскольку речь идёт об алкогольных напитках — сайт и мастер-классы
            только для совершеннолетних, 18+. Призываем употреблять умеренно, с достоинством
            и уважением к традициям. При сборе трав будьте внимательны, у некоторых растений
            есть ядовитые двойники.
          </p>
        </div>
      </section>

      <footer className="site">
        <div className="brand">Настойки.pro</div>
        <div style={{ marginTop: 6 }}>
          проект{" "}
          <a href={CHANNEL_URL} target="_blank" rel="noopener noreferrer">«Корни, Травы, Дистиллят»</a>
          {" "}· мастер отвечает по корпусу оцифрованных старинных книг
        </div>
      </footer>
    </main>
  );
}

/* ── brand logos ── */
function IcTelegram({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="tgGrad" x1="120" y1="0" x2="120" y2="240" gradientUnits="userSpaceOnUse">
          <stop stopColor="#2AABEE" />
          <stop offset="1" stopColor="#229ED9" />
        </linearGradient>
      </defs>
      <circle cx="120" cy="120" r="120" fill="url(#tgGrad)" />
      <path
        fill="#fff"
        d="M53.6 118.7C88.5 103.5 111.8 93.5 123.4 88.7c33.3-13.9 40.2-16.3 44.7-16.3 1 0 3.2.2 4.6 1.4.6.5 1 1.4 1.1 2.4.1.7.3 2.4.2 3.7-1.4 14.9-7.5 51.1-10.6 67.8-1.3 7.1-3.9 9.4-6.4 9.7-5.4.5-9.6-3.6-14.8-7-8.2-5.4-12.8-8.7-20.8-14-9.2-6.1-3.2-9.4 2-14.9 1.4-1.4 25-22.9 25.4-24.8.1-.3.1-1.3-.5-1.9-.6-.5-1.5-.3-2.2-.2-.9.2-15.5 9.9-43.8 29-4.1 2.8-7.9 4.2-11.2 4.1-3.7-.1-10.8-2.1-16.1-3.8-6.5-2.1-11.6-3.2-11.2-6.8.2-1.9 2.8-3.8 7.6-5.9z"
      />
    </svg>
  );
}
function IcMax({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 42 42" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="maxGrad" x1="0" y1="0" x2="42" y2="42" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0A7CFF" />
          <stop offset="1" stopColor="#A340FF" />
        </linearGradient>
      </defs>
      <path
        fill="url(#maxGrad)"
        fillRule="evenodd"
        clipRule="evenodd"
        d="M21.47 41.88c-4.11 0-6.02-.6-9.34-3-2.1 2.7-8.75 4.81-9.04 1.2 0-2.71-.6-5-1.28-7.5C1 29.5.08 26.07.08 21.1.08 9.23 9.82.3 21.36.3c11.55 0 20.6 9.37 20.6 20.91a20.6 20.6 0 0 1-20.49 20.67m.17-31.32c-5.62-.29-10 3.6-10.97 9.7-.8 5.05.62 11.2 1.83 11.52.58.14 2.04-1.04 2.95-1.95a10.4 10.4 0 0 0 5.08 1.81 10.7 10.7 0 0 0 11.19-9.97 10.7 10.7 0 0 0-10.08-11.1Z"
      />
    </svg>
  );
}
