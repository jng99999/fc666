import type { Metadata } from "next";
import "./globals.css";
import "./workstation.css";
export const metadata: Metadata = {title: "FC666 · Quant Workstation", description: "FC666 development foundation"};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  const frontendOnly=Boolean(process.env.VERCEL)&&!process.env.API_BASE_URL;
  return <html lang="zh-CN"><body>{frontendOnly?<aside className="deployment-notice" role="status">当前为前端预览，行情和研究数据服务尚未连接。<a href="/system">查看服务状态</a></aside>:null}{children}</body></html>;
}
