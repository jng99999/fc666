import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title: "FC666 · Quant Workstation", description: "FC666 development foundation"};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
