import type { MetadataRoute } from "next";

const SITE = "https://nastoiki.pro";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: SITE, changeFrequency: "weekly", priority: 1 },
  ];
}
