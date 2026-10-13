import type { NextConfig } from "next";
import {apiOrigin} from "./lib/api-origin";
import {marketSocketUrl} from "./lib/market-socket-url";
// Fail the deployment build on malformed or insecure public socket configuration.
if (process.env.NEXT_PUBLIC_MARKET_WS_URL) marketSocketUrl(process.env.NEXT_PUBLIC_MARKET_WS_URL,{protocol:"https:",host:"localhost"});
const backendOrigin=apiOrigin(process.env.API_BASE_URL,process.env.VERCEL==='1');
const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return [{source:"/stream/:path*",destination:`${backendOrigin}/ws/:path*`}];
  },
};
export default config;
