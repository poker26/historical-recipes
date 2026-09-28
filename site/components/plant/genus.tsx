// Родовая карточка: виды-члены плитками, сводные действия с числом видов, вещества
// и рецепты рода. Своих фактов у рода нет: всё с атрибуцией по видам.
import Link from "next/link";
import { Tile } from "../common";
import { fmtInt, plantHref, pluralRu } from "../../lib/api";
import { capFirst, displayName, photoSourceOf, recipeMeta, type GenusCard, type Photo, type PlantSummary } from "../../lib/api-plant";
import { Block, More } from "./bits";
import { PlantHead, SafetyPanel, Toc } from "./head";
import { ActionLink, IndicationsLine } from "./uses";
import { CardFooter } from "./refs";

const nSpeciesGen = (n: number) => `${fmtInt(n)} ${pluralRu(n, "вида", "видов", "видов")}`;

export function GenusView({ card, photos, plate }: { card: GenusCard; photos: PlantSummary[]; plate: string | null }) {
  const byId = new Map(photos.map((p) => [p.id, p]));
  const self = byId.get(card.id);
  const photo: Photo | null = self?.photo_url
    ? { url: self.photo_url, attribution: self.photo_attribution ?? null, license: null, source: photoSourceOf(self.photo_url) }
    : null;
  const byName = new Map(card.members.map((m) => [m.name, m]));
  const members = card.members
    .map((m, i) => ({ m, s: byId.get(m.id), i }))
    .sort((a, b) => (b.s?.photo_url ? 1 : 0) - (a.s?.photo_url ? 1 : 0) || (b.s?.uses_count ?? 0) - (a.s?.uses_count ?? 0) || a.i - b.i);
  const dangerous = card.safety?.dangerous_members ?? [];

  const toc = [
    card.members.length ? { id: "members", label: "Виды рода" } : null,
    card.uses.length ? { id: "uses", label: "Применение" } : null,
    card.compounds.length ? { id: "compounds", label: "Состав" } : null,
    card.recipes.length ? { id: "recipes", label: "Рецепты" } : null,
  ].filter((x): x is { id: string; label: string } => !!x);

  const speciesLinks = (names: string[]) =>
    names.map((n, i) => {
      const m = byName.get(n);
      return (
        <span key={i}>
          {i > 0 ? ", " : null}
          {m ? <Link href={plantHref(m.id, m.latin)}>{displayName(n)}</Link> : displayName(n)}
        </span>
      );
    });

  return (
    <div className="pc">
      <PlantHead
        name={displayName(card.name)}
        latin={card.latin}
        kingdom={card.kingdom}
        badge={card.kingdom === "гриб" ? "род грибов" : "род"}
        photo={photo}
        plate={plate}
        showSources={false}
        stats={`В атласе ${nSpeciesGen(card.memberCount)} этого рода.${card.recipesTotal ? ` Ещё ${fmtInt(card.recipesTotal)} ${pluralRu(card.recipesTotal, "рецепт называет", "рецепта называют", "рецептов называют")} род, не уточняя вид.` : ""}`}
        safety={
          card.safety ? (
            <SafetyPanel
              safety={{ ...card.safety, rationale: "Виды рода опасны по-разному. Здесь указан уровень самого опасного из них, а точный ответ есть в карточке каждого вида." }}
              isToxic={false}
              extra={
                dangerous.length ? (
                  <div className="pc-parts">
                    <span className="pc-label">Опасные виды</span>
                    {dangerous.map((d) => {
                      const m = card.members.find((x) => x.id === d.id);
                      return (
                        <Link key={d.id} href={plantHref(d.id, m?.latin)} className="chip chip-danger">
                          {displayName(d.name)}
                        </Link>
                      );
                    })}
                  </div>
                ) : null
              }
            />
          ) : null
        }
        lines={
          <p className="pc-note muted small">
            В карточке рода собраны записи о всех его видах. У каждого применения и вещества подписан вид, у которого его
            нашли. Здесь же рецепты, где книга называет только род.
          </p>
        }
      />
      <Toc items={toc} />

      {members.length ? (
        <Block id="members" title="Виды рода" lead="Открой вид, чтобы увидеть цитаты, состав и рецепты именно для него.">
          <div className="tiles tiles-sm">
            {members.map(({ m, s }) => (
              <Tile
                key={m.id}
                href={plantHref(m.id, m.latin)}
                name={displayName(m.name)}
                latin={m.latin}
                photo={s?.photo_url ?? null}
                meta={s?.uses_count ? `${fmtInt(s.uses_count)} ${pluralRu(s.uses_count, "запись", "записи", "записей")} о применении` : null}
                tags={s?.safety_level === 4 ? [{ label: "смертельно ядовито", warn: true }] : s?.safety_level === 3 ? [{ label: "ядовито в больших дозах", warn: true }] : undefined}
              />
            ))}
          </div>
        </Block>
      ) : null}

      {card.uses.length ? (
        <Block id="uses" title="Применение" lead="Действия, о которых книги пишут у видов этого рода. Сначала те, что встречаются у большего числа видов.">
          <div className="pc-mini-grid">
            {card.uses.map((u, i) => (
              <div key={i} className="pc-use pc-use-mini">
                <div className="pc-use-head">
                  <h3>{capFirst(u.action)}</h3>
                  <span className="muted small">у {nSpeciesGen(u.nSpecies)}</span>
                  <ActionLink action={u.action} />
                </div>
                {u.species.length ? (
                  <p className="pc-line">
                    <span className="pc-label">Виды</span><span>{speciesLinks(u.species)}</span>
                  </p>
                ) : null}
                <IndicationsLine items={u.indications} />
                {u.sources.length ? <p className="pc-line small"><span className="pc-label">Книги</span><span className="muted">{u.sources.join("; ")}</span></p> : null}
              </div>
            ))}
          </div>
        </Block>
      ) : null}

      {card.compounds.length ? (
        <Block id="compounds" title="Состав" lead="Вещества, которые книги находят у видов рода, и у скольких видов они названы.">
          <ul className="pc-cmp-list pc-cmp-cols">
            {card.compounds.map((c, i) => (
              <li key={i}>
                <span className="pc-cmp-name">{c.compound}</span>
                <span className="muted">, у {nSpeciesGen(c.nSpecies)}</span>
                {c.species.length ? <span className="pc-cmp-src">{speciesLinks(c.species)}</span> : null}
              </li>
            ))}
          </ul>
        </Block>
      ) : null}

      {card.recipes.length ? (
        <Block id="recipes" title="Рецепты рода" lead="Рецепты, где книга называет род, не уточняя вид.">
          <div className="pc-recipes">
            {card.recipes.slice(0, 6).map((r) => (
              <Link key={r.id} href={`/recipe/${r.id}`} className="card card-tight pc-recipe">
                <b>{r.name}</b>
                <div className="small muted">{recipeMeta(r)}</div>
              </Link>
            ))}
          </div>
          {card.recipes.length > 6 ? (
            <More summary={`ещё ${fmtInt(card.recipes.length - 6)} ${pluralRu(card.recipes.length - 6, "рецепт", "рецепта", "рецептов")}`}>
              <div className="pc-recipes">
                {card.recipes.slice(6).map((r) => (
                  <Link key={r.id} href={`/recipe/${r.id}`} className="card card-tight pc-recipe">
                    <b>{r.name}</b>
                    <div className="small muted">{recipeMeta(r)}</div>
                  </Link>
                ))}
              </div>
            </More>
          ) : null}
          {card.recipesTotal > card.recipes.length ? (
            <p className="pc-all">
              <Link href={`/recipes?plant_id=${card.id}`} className="more">
                Все рецепты рода, их {fmtInt(card.recipesTotal)} →
              </Link>
            </p>
          ) : null}
        </Block>
      ) : null}

      <CardFooter yearMin={null} yearMax={null} />
    </div>
  );
}
