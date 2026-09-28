import type { Metadata } from "next";
import { SafetyLandingPage, safetyLanding, safetyLandingMeta } from "../../../components/atlas/SafetyLanding";
import "../atlas.css";

const L = safetyLanding("/atlas/edible-mushrooms");
type Props = { searchParams: { page?: string | string[] } };

export async function generateMetadata({ searchParams }: Props): Promise<Metadata> {
  return safetyLandingMeta(L, searchParams.page);
}

export default function Page({ searchParams }: Props) {
  return <SafetyLandingPage l={L} pageRaw={searchParams.page} />;
}
