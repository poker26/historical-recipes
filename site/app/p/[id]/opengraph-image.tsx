import { ImageResponse } from "next/og";
import { getProfile } from "../../lib";
import { profileCard, CARD_FONTS } from "../../og-card";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Паспорт натуралиста — Что растёт";

export default async function Image({ params }: { params: { id: string } }) {
  const p = await getProfile(params.id);
  return new ImageResponse(profileCard(p, "og"), { ...size, fonts: CARD_FONTS });
}
