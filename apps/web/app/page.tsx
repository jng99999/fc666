"use client";
import {useState} from "react";
import AnalysisWorkspace from "./analysis/analysis-workspace";
import Terminal from "../components/terminal";

export default function Home() {
  const [depthOpen,setDepthOpen]=useState(false);
  return <><AnalysisWorkspace market/><details className="market-depth-workspace" onToggle={event=>setDepthOpen(event.currentTarget.open)}><summary>深度、逐笔成交与连接详情</summary>{depthOpen?<Terminal embedded/>:null}</details></>;
}
