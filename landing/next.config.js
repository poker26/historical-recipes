/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server bundle for a small Docker runtime image.
  output: "standalone",
  reactStrictMode: true,
  // iNaturalist photos on the quest pages.
  images: {
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
};

module.exports = nextConfig;
