import Link from "next/link";
import { Header, Footer } from "./ui";

export default function NotFound() {
  return (
    <>
      <Header />
      <section className="card" style={{ marginTop: 32, textAlign: "center", padding: "48px 24px" }}>
        <h1 style={{ fontSize: 30 }}>Такой страницы нет</h1>
        <p className="muted" style={{ maxWidth: 480, margin: "8px auto 20px" }}>
          Ссылка могла устареть, а карточка могла переехать после сверки названий.
          Попробуйте найти растение через поиск или откройте атлас.
        </p>
        <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          <Link href="/atlas" className="btn btn-primary">Открыть атлас</Link>
          <Link href="/" className="btn btn-ghost">На главную</Link>
        </div>
      </section>
      <Footer />
    </>
  );
}
