import { ImageResponse } from "next/og";
import { getProfile } from "../../../lib";
import { profileCard, CARD_FONTS } from "../../../og-card";

// Square share card (1080×1080) — the app fetches this and shares it as an IMAGE
// (Stories/posts), not just a link. Same renderer as the OG unfurl.
export async function GET(_req: Request, { params }: { params: { id: string } }) {
  const p = await getProfile(params.id);
  return new ImageResponse(profileCard(p, "square"), {
    width: 1080,
    height: 1080,
    fonts: CARD_FONTS,
  });
}
