// Таблица ингредиентов рецепта: как в книге, что это, сколько, какое растение атласа.
// На узком экране строки превращаются в карточки с подписями из data-label (recipes.css).
import Link from "next/link";
import { LeafGlyph } from "../common";
import { plantHref } from "../../lib/api";
import { amountText, type Ingredient } from "../../lib/api-recipes";

function SafetyChip({ i }: { i: Ingredient }) {
  if (i.plant_safety_level === 4) return <span className="chip chip-danger">смертельно ядовито</span>;
  if (i.plant_safety_level === 3) return <span className="chip chip-danger">ядовито в больших дозах</span>;
  // Старый флаг ядовитости учитываем, только пока у карточки нет уровня.
  if (i.plant_safety_level == null && i.plant_is_toxic) return <span className="chip chip-danger">ядовито</span>;
  return null;
}

export function IngredientsTable({ items }: { items: Ingredient[] }) {
  return (
    <table className="table rc-ings">
      <thead>
        <tr>
          <th scope="col">Как в книге</th>
          <th scope="col">Что это</th>
          <th scope="col">Сколько</th>
          <th scope="col">Растение в атласе</th>
        </tr>
      </thead>
      <tbody>
        {items.map((i) => {
          const a = amountText(i);
          return (
            <tr key={i.id}>
              <td data-label="Как в книге" className="rc-ing-orig">{i.original_name || i.name || "—"}</td>
              <td data-label="Что это">{i.name || "—"}</td>
              <td data-label="Сколько">
                {a.book || <span className="muted">не указано</span>}
                {a.modern ? <div className="small muted">≈ {a.modern} по-современному</div> : null}
              </td>
              <td data-label="Растение">
                {i.plant_id ? (
                  <div className="rc-ing-plant">
                    <Link href={plantHref(i.plant_id, i.plant_latin)} className="rc-ing-link">
                      <span className="rc-thumb" aria-hidden="true">
                        {i.plant_photo ? <img src={i.plant_photo} alt="" loading="lazy" /> : <LeafGlyph />}
                      </span>
                      <span>
                        {i.plant_name || i.name}
                        {i.plant_latin ? <span className="latin small"> {i.plant_latin}</span> : null}
                      </span>
                    </Link>
                    <SafetyChip i={i} />
                  </div>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
