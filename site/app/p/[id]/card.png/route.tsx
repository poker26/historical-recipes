import { ImageResponse } from "next/og";
import { getProfile } from "../../../lib";
import { profileCard, CARD_FONTS } from "../../../og-card";

// Always re-render per request: the card must reflect the CURRENT avatar/level. A
// statically-cached route once served a pre-avatar render («венок без аватарки»).
export const dynamic = "force-dynamic";
export const revalidate = 0;

// Square share card (1080×1080) — the app fetches this and shares it as an IMAGE
// (Stories/posts), not just a link. Same renderer as the OG unfurl.
export async function GET(_req: Request, { params }: { params: { id: string } }) {
  const p = await getProfile(params.id);
  const img = new ImageResponse(profileCard(p, "square"), {
    width: 1080,
    height: 1080,
    fonts: CARD_FONTS,
  });
  // next/og hard-codes `cache-control: immutable, max-age=1y`, which froze a card
  // rendered while the avatar was still empty. Override with a short TTL so a changed
  // avatar/level shows up on the next share within minutes.
  img.headers.set("cache-control", "public, max-age=120, must-revalidate");
  return img;
}
