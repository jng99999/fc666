import type { NextConfig } from "next";
import {marketSocketUrl} from "./lib/market-socket-url";
// Fail the deployment build on malformed or insecure public socket configuration.
if (process.env.NEXT_PUBLIC_MARKET_WS_URL) marketSocketUrl(process.env.NEXT_PUBLIC_MARKET_WS_URL,{protocol:"https:",host:"localhost"});
const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return [{source:"/stream/:path*",destination:`${process.env.API_BASE_URL ?? "http://127.0.0.1:8000"}/ws/:path*`}];
  },
};
export default config;
