import type { Metadata, Viewport } from "next";
import "./globals.css";
import "./workstation.css";
import "./workspace.css";
import "./apple-ui.css";
import "./workstation-ui.css";
import WorkspaceNav from "../components/workspace-nav";
export const metadata: Metadata = {title: "FC666 · Quant Workstation", description: "FC666 development foundation"};
export const viewport: Viewport = {width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#f7f8fa"};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  const frontendOnly=Boolean(process.env.VERCEL)&&!process.env.API_BASE_URL;
  return <html lang="zh-CN"><body className="apple-ui"><WorkspaceNav/>{frontendOnly?<aside className="deployment-notice" role="status">当前为前端预览，行情和研究数据服务尚未连接。<a href="/system">查看服务状态</a></aside>:null}{children}</body></html>;
}
