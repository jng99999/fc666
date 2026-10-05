import type { NextConfig } from "next";
const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return [{source:"/stream/:path*",destination:`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/ws/:path*`}];
  },
};
export default config;
