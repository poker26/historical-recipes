import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // личные и служебные адреса: приглашения, картинки шэров, поиск с параметрами
        disallow: ["/i/", "/search", "/p/*/card.png", "/plant/*/card.png", "/collection"],
      },
    ],
    sitemap: "https://botanik.fun/sitemap.xml",
  };
}
