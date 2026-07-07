/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server bundle for a small Docker runtime image.
  output: "standalone",
  reactStrictMode: true,
  images: {
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
};

module.exports = nextConfig;
