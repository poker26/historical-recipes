// Карточка вида: /atlas/{слаг} (urtica-dioica-718405) или /atlas/{uuid}.
// Канонический адрес строит plantHref(): пришли по UUID или по устаревшему слагу —
// отвечаем 308 на канонический. Род (rank = genus) рисуется своей формой.
import type { Metadata } from "next";
import Link from "next/link";
import { Fragment } from "react";
import { notFound, permanentRedirect } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Empty } from "../../../components/common";
import { SITE_URL, excerpt, plantHref, plantSlug } from "../../../lib/api";
import {
  displayName, getCompoundInsights, getFieldView, getGenusPhotos, getObservations, getPairings, getPlantCard, getPlantRecipes,
  jsonLd, largePhoto, normKind, passesGate, photoSourceOf, plateExists, plateKey, plateUrl, queryFrom, quoteOf,
  resolvePlantParam, sameName, type GenusCard, type Photo, type PlantCard, type SpeciesCard,
} from "../../../lib/api-plant";
import { PlantHead, SafetyPanel, Toc } from "../../../components/plant/head";
import { EssayBlock, SafetyBlock, UsesBlock } from "../../../components/plant/uses";
import { CompoundsBlock } from "../../../components/plant/composition";
import { HabitatBlock, HarvestBlock } from "../../../components/plant/nature";
import { KitchenBlock, PairingsBlock } from "../../../components/plant/kitchen";
import { CardFooter, CareBlock, KinBlock, NamesBlock, SourcesBlock } from "../../../components/plant/refs";
import { GenusView } from "../../../components/plant/genus";
import { nBooksDat, nQuotes } from "../../../components/plant/bits";
import "../../plant/plant.css";

type SearchParams = Record<string, string | string[] | undefined>;
type Props = { params: { slug: string }; searchParams: SearchParams };

const OG_BASE = { siteName: "Что растёт", locale: "ru_RU", type: "article" as const };

function decoded(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** Фото, которое можно показать: только с автором или лицензией. */
function creditedPhoto(p: Photo | null): Photo | null {
  return p && (p.attribution || p.license) ? p : null;
}

async function genusPhoto(card: GenusCard): Promise<Photo | null> {
  if (!card.latin) return null;
  const self = (await getGenusPhotos(card.latin)).find((p) => p.id === card.id);
  return self?.photo_url
    ? creditedPhoto({ url: self.photo_url, attribution: self.photo_attribution ?? null, license: null, source: photoSourceOf(self.photo_url) })
    : null;
}

async function loadCard(slug: string): Promise<{ state: "ok"; card: PlantCard } | { state: "missing" } | { state: "error" }> {
  const r = await resolvePlantParam(slug);
  if (r.state !== "ok") return r;
  return getPlantCard(r.id);
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const res = await loadCard(params.slug);
  if (res.state !== "ok") return { title: "Карточка вида", robots: { index: false, follow: true } };
  const card = res.card;
  const name = displayName(card.name);
  const title = card.latin ? `${name} (${card.latin})` : name;
  const canonical = SITE_URL + plantHref(card.id, card.latin);
  let description: string;
  let image: string | null = null;
  let photoOk = false;
  if (card.kind === "genus") {
    const photo = await genusPhoto(card);
    image = photo ? largePhoto(photo.url) : null;
    photoOk = !!photo;
    description = `Род ${name}${card.latin ? ` (${card.latin})` : ""} в атласе «Что растёт»: виды рода, чем их применяли по книгам, вещества и рецепты.`;
  } else {
    const field = await getFieldView(card.id);
    const lead = quoteOf(field?.lead_fact);
    description =
      field?.verdict?.trim() || lead?.text || card.firstQuote || card.description ||
      `${name}: применение, состав, сбор и рецепты по книгам с годом и страницей.`;
    const photo = creditedPhoto(card.photo);
    photoOk = !!photo;
    if (photo) image = largePhoto(photo.url);
    else {
      const key = plateKey(card.latin);
      if (key && (await plateExists(key))) image = plateUrl(key);
    }
  }
  const index = passesGate({ kingdom: card.kingdom, name: card.name, latin: card.latin, photo: photoOk });
  return {
    title,
    description: excerpt(description, 160),
    alternates: { canonical },
    openGraph: { ...OG_BASE, title, description: excerpt(description, 200), url: canonical, images: image ? [{ url: image }] : undefined },
    robots: index ? { index: true, follow: true } : { index: false, follow: true },
  };
}

function Unavailable() {
  return (
    <>
      <Header active="/atlas" />
      <div className="section">
        <Empty>
          Карточку сейчас не удалось загрузить: справочник не ответил. Обнови страницу через минуту или открой{" "}
          <Link href="/atlas">атлас</Link>.
        </Empty>
      </div>
      <Footer />
    </>
  );
}

export default async function AtlasPlantPage({ params, searchParams }: Props) {
  const res = await loadCard(params.slug);
  if (res.state === "missing") notFound();
  if (res.state === "error") return <Unavailable />;
  const card = res.card;

  // Канонический адрес: permanentRedirect бросает исключение, поэтому вне try/catch.
  const href = plantHref(card.id, card.latin);
  if (decoded(params.slug) !== plantSlug(card.id, card.latin)) permanentRedirect(href + queryFrom(searchParams));

  if (card.kind === "genus") {
    const photo = await genusPhoto(card);
    const photos = card.latin ? await getGenusPhotos(card.latin) : [];
    const ld = {
      "@context": "https://schema.org",
      "@type": "Taxon",
      name: displayName(card.name),
      scientificName: card.latin ?? undefined,
      taxonRank: "genus",
      url: SITE_URL + href,
      image: photo ? largePhoto(photo.url) : undefined,
    };
    return (
      <>
        <Header active="/atlas" />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(ld) }} />
        <GenusView card={card} photos={photos} plate={null} />
        <Footer />
      </>
    );
  }

  return renderSpecies(card, href, searchParams);
}

async function renderSpecies(card: SpeciesCard, href: string, searchParams: SearchParams) {
  const kind = normKind(searchParams.kind);
  const photoFromCard = creditedPhoto(card.photo);
  const key = photoFromCard ? null : plateKey(card.latin);
  const [field, recipes, pairings, insights, obs, hasPlate] = await Promise.all([
    getFieldView(card.id),
    getPlantRecipes(card.id, kind),
    getPairings(card.id),
    getCompoundInsights(card.id),
    card.inatTaxonId ? getObservations(card.id) : Promise.resolve(null),
    key ? plateExists(key) : Promise.resolve(false),
  ]);
  const fieldPhoto = field?.photo_url
    ? creditedPhoto({ url: field.photo_url, attribution: field.photo_attribution ?? null, license: field.photo_license ?? null, source: field.photo_source ?? null })
    : null;
  const photo = photoFromCard ?? fieldPhoto;
  const plate = !photo && key && hasPlate ? plateUrl(key) : null;

  const name = displayName(card.name);
  const modern = card.nameModern && !sameName(card.nameModern, card.name) ? card.nameModern : null;
  const houseplant = field?.origin === "houseplant";
  const badge = card.kingdom === "гриб" ? "гриб" : houseplant ? "комнатное растение" : null;
  const stats = card.bookCount
    ? `В атласе по ${nBooksDat(card.bookCount)}${card.usesTotal ? `, ${nQuotes(card.usesTotal)} о применении` : ""}.`
    : null;

  // Блоки собираем заранее: пустой блок возвращает null и не попадает в оглавление.
  const blocks = [
    { id: "essay", label: "Очерк", el: EssayBlock({ card, field }) },
    { id: "safety", label: "Безопасность", el: SafetyBlock({ card, field }) },
    { id: "uses", label: "Применение", el: UsesBlock({ card }) },
    { id: "compounds", label: "Состав", el: CompoundsBlock({ card, insights }) },
    { id: "harvest", label: "Сбор", el: HarvestBlock({ card, field }) },
    { id: "habitat", label: "Где растёт", el: HabitatBlock({ card, field, obs }) },
    { id: "kitchen", label: "Кулинария", el: KitchenBlock({ card, field, recipes, kind, base: href }) },
    { id: "pairings", label: "С чем сочетают", el: PairingsBlock({ pairings }) },
    { id: "care", label: "Уход", el: CareBlock({ card, field }) },
    { id: "names", label: "Имена", el: NamesBlock({ card }) },
    { id: "sources", label: "Источники", el: SourcesBlock({ card }) },
    { id: "kin", label: "Родня", el: KinBlock({ card }) },
  ].filter((b) => b.el);

  const ld = {
    "@context": "https://schema.org",
    "@type": "Taxon",
    name,
    scientificName: card.latin ?? undefined,
    alternateName: card.names.slice(0, 5),
    taxonRank: "species",
    url: SITE_URL + href,
    image: photo ? largePhoto(photo.url) : plate ?? undefined,
    parentTaxon: card.parent
      ? { "@type": "Taxon", name: card.parent.latin || displayName(card.parent.name), url: SITE_URL + plantHref(card.parent.id, card.parent.latin) }
      : undefined,
  };

  return (
    <>
      <Header active="/atlas" />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(ld) }} />
      <div className="pc">
        <PlantHead
          name={name}
          latin={card.latin}
          nameModern={modern}
          family={card.family}
          familyLatin={card.familyLatin}
          kingdom={card.kingdom}
          badge={badge}
          photo={photo}
          plate={plate}
          safety={<SafetyPanel safety={card.safety ?? field?.safety ?? null} isToxic={card.isToxic || !!field?.is_toxic} />}
          stats={stats}
          showSources={blocks.some((b) => b.id === "sources")}
        />
        <Toc items={blocks.map(({ id, label }) => ({ id, label }))} />
        {blocks.map((b) => (
          <Fragment key={b.id}>{b.el}</Fragment>
        ))}
        {!blocks.length ? (
          <div className="section">
            <Empty>
              В корпусе пока нет записей об этом виде, кроме имени. Загляни в <Link href="/atlas">атлас</Link>: там собраны виды с цитатами и
              рецептами.
            </Empty>
          </div>
        ) : null}
        <CardFooter yearMin={card.yearMin} yearMax={card.yearMax} />
      </div>
      <Footer />
    </>
  );
}

