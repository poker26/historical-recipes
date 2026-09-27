import { ImageResponse } from "next/og";
import { getPlant } from "../../../lib";
import { plantCard, CARD_FONTS } from "../../../og-card";

// Square «находка» share card (1080×1080) — the app fetches this and shares it as
// an image. The plant photo is fetched server-side → data URI (satori's remote-image
// fetch is fragile; a failed photo falls back to the leaf placeholder).
export async function GET(_req: Request, { params }: { params: { id: string } }) {
  const p = await getPlant(params.id);
  let photo: string | null = null;
  if (p?.photo_url) {
    try {
      const r = await fetch(p.photo_url, { signal: AbortSignal.timeout(6000) });
      if (r.ok) photo = "data:image/jpeg;base64," + Buffer.from(await r.arrayBuffer()).toString("base64");
    } catch {
      /* leaf placeholder */
    }
  }
  return new ImageResponse(plantCard(p, photo), { width: 1080, height: 1080, fonts: CARD_FONTS });
}
