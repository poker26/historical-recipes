// Сброс кэша карточек сразу после правки данных. Бэкенд зовёт этот адрес изнутри
// docker-сети после слияния карточек, смены латыни и перегенерации очерка; без него
// исправление доходит до страницы только через срок кэша, шесть часов.
//
// Адрес снаружи тоже открыт (nginx отдаёт сайту всё), поэтому без секрета из
// REVALIDATE_SECRET он ничего не делает и отвечает 404, как несуществующий.
import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { isUuid } from "../../../lib/api";
import { plantTag } from "../../../lib/api-plant";

export const dynamic = "force-dynamic";

const MAX_IDS = 1000;

export async function POST(req: Request) {
  const secret = process.env.REVALIDATE_SECRET;
  if (!secret || req.headers.get("x-revalidate-secret") !== secret) {
    return new NextResponse("Not Found", { status: 404 });
  }
  let body: { ids?: unknown } = {};
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "нужен JSON вида {\"ids\": [uuid, …]}" }, { status: 400 });
  }
  const ids = (Array.isArray(body.ids) ? body.ids : [])
    .filter((x): x is string => typeof x === "string" && isUuid(x))
    .slice(0, MAX_IDS);
  for (const id of ids) revalidateTag(plantTag(id));
  return NextResponse.json({ ok: true, plants: ids.length });
}
