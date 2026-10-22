"use client";
import {useEffect,useState} from "react";
import {CandleSchema,SnapshotSchema,MarketProvider} from "../lib/market";
import {marketSocketUrl} from "../lib/market-socket-url";
import {z} from "zod";
type Result="checking"|"passed"|"failed";
type Checks={api:Result;history:Result;socket:Result};
const initial:Checks={api:"checking",history:"checking",socket:"checking"};
const labels={checking:"检查中…",passed:"本次检查通过",failed:"未通过"};
export default function DeploymentCheck({hosted,backendConfigured}:{hosted:boolean;backendConfigured:boolean}) {
  const [checks,setChecks]=useState<Checks>(initial);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{
    let disposed=false,socketDone=false;
    const controller=new AbortController();
    const update=(key:keyof Checks,result:Result)=>{if(!disposed)setChecks(value=>({...value,[key]:result}));};
    setChecks(initial);
    async function http(key:"api"|"history",path:string) {
      try {
        const response=await fetch(path,{cache:"no-store",signal:controller.signal});
        if(!response.ok)throw new Error("unavailable");
        const data=await response.json();
        if(key==="api") {
          if(data.status!=="ready"||data.checks?.database!==true||data.checks?.schema!==true||data.checks?.redis!==true||data.trading_enabled!==false)throw new Error("not ready");
        } else {
          const candles=z.array(CandleSchema).max(10).parse(data);
          if(!candles.length||candles.some(c=>c.instrument_id!==`${MarketProvider}:spot:BTC-USDT`||c.timeframe!=="1m"||!c.is_closed))throw new Error("no historical data");
        }
        update(key,"passed");
      }catch{update(key,"failed");}
    }
    void http("api","/api/health");
    void http("history","/api/market/candles?symbol=BTCUSDT&timeframe=1m&limit=10");
    let socket:WebSocket|undefined;
    function finishSocket(result:Result) {
      if(socketDone)return;
      socketDone=true;update("socket",result);socket?.close();
    }
    try {
      socket=new WebSocket(marketSocketUrl(process.env.NEXT_PUBLIC_MARKET_WS_URL,location));
      socket.onopen=()=>socket?.send(JSON.stringify({action:"subscribe",symbols:["BTCUSDT"],channels:["ticker"]}));
      socket.onmessage=event=>{
        try {
          const data=JSON.parse(event.data);
          if(data.type==="subscribed"||data.type==="heartbeat")return;
          const value=SnapshotSchema.parse(data);
          const age=Date.now()-Date.parse(value.received_at);
          finishSocket(value.symbol==="BTCUSDT"&&value.channel==="ticker"&&age>=-5000&&age<=15000?"passed":"failed");
        }catch{finishSocket("failed");}
      };
      socket.onerror=()=>finishSocket("failed");
      socket.onclose=()=>finishSocket("failed");
    }catch{finishSocket("failed");}
    const timeout=setTimeout(()=>{controller.abort();finishSocket("failed");},8000);
    return ()=>{disposed=true;clearTimeout(timeout);controller.abort();socket?.close();};
  },[revision]);
  const complete=Object.values(checks).every(value=>value!=="checking");
  return <section className="health" aria-labelledby="deployment-heading">
    <h2 id="deployment-heading">站点连接检查</h2>
    <p>{hosted&&!backendConfigured?"当前站点尚未配置数据服务。行情和研究功能需要连接后端。":"检查当前站点的数据服务和实时行情连接。"}</p>
    <dl>{([["api","数据服务"],["history","历史 K 线"],["socket","实时行情连接"]] as const).map(([key,label])=><div key={key}><dt>{label}</dt><dd data-testid={`deployment-${key}`} className={checks[key]==="passed"?"ok":"muted"}>{labels[checks[key]]}</dd></div>)}</dl>
    {complete&&Object.values(checks).some(value=>value==="failed")?<p role="alert">连接检查未全部通过，请检查数据服务部署和实时连接配置。</p>:null}
    <p>结果仅代表本次检查；研究任务和账户功能需另行验证。</p>
    <button type="button" disabled={!complete} onClick={()=>setRevision(value=>value+1)}>重新检查连接</button>
  </section>;
}
