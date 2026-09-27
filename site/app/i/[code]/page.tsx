import type { Metadata } from "next";
import { headers } from "next/headers";
import { recordInviteClick } from "../../lib";
import { Header, Footer, DownloadButtons, ProfileCrest } from "../../ui";

// Страница живая: она СОВЕРШАЕТ действие (запоминает переход), кешировать её нельзя.
export const dynamic = "force-dynamic";
export const revalidate = 0;

type Params = { params: { code: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  return {
    title: "Приглашение · Что растёт",
    description: "Тебя зовут определять растения и грибы по фото и собирать свой гербарий.",
    robots: { index: false },     // личные ссылки в поиске ни к чему
  };
}

export default async function InvitePage({ params }: Params) {
  // Адрес посетителя нужен серверу, чтобы после установки из магазина узнать этого
  // же человека: код в ссылке до приложения не доезжает — между ними магазин.
  const h = headers();
  const ip = (h.get("x-forwarded-for") || "").split(",")[0].trim() || null;
  const host = await recordInviteClick(params.code, ip, h.get("user-agent"));
  const nick = host?.host_nick;

  return (
    <>
      <Header />
      <section className="section card" style={{ textAlign: "center", padding: "40px 24px" }}>
        {host?.host_avatar ? (
          <div style={{ display: "flex", justifyContent: "center", marginBottom: 18 }}>
            <ProfileCrest avatar={host.host_avatar} level={1} size={120} />
          </div>
        ) : null}
        <h1 style={{ margin: "0 0 10px", fontSize: 30 }}>
          {nick ? `${nick} зовёт тебя в «Что растёт»` : "Тебя зовут в «Что растёт»"}
        </h1>
        <p style={{ color: "#3f4a43", margin: "0 auto 8px", maxWidth: 560 }}>
          Сфотографируй растение или гриб — приложение назовёт вид и расскажет о нём:
          где искать, когда собирать, что из него готовили и чем оно опасно. Всё собрано
          из книг, которым до двухсот лет.
        </p>
        <p style={{ color: "#6b7368", margin: "0 auto 24px", maxWidth: 560, fontSize: 14 }}>
          Ставь приложение и открывай — приглашение подхватится само, вводить ничего
          не нужно. {nick ? `${nick} получит за тебя значок «Проводник».` : ""}
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>
      <Footer />
    </>
  );
}
