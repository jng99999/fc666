"use client";
import {useEffect} from "react";
import {SnapshotSchema,type Interval} from "../lib/market";
import {useMarketStore} from "../lib/market-store";
export default function MarketSocket({timeframe}:{timeframe:Interval}) {
  useEffect(() => {
    let disposed=false,attempt=0,socket:WebSocket|null=null,timer:ReturnType<typeof setTimeout>|undefined;
    function connect() {
      useMarketStore.getState().setConnection(attempt ? "retrying":"connecting");
      const ws=new WebSocket(`${location.protocol==="https:" ? "wss":"ws"}://${location.host}/stream/v1/market`);socket=ws;
      ws.onopen=() => ws.send(JSON.stringify({action:"subscribe",symbols:["BTCUSDT","ETHUSDT"],channels:["ticker","book","trade",`candle.${timeframe}`]}));
      ws.onmessage=event => {
        if (disposed || socket!==ws) return;
        try {
          const message=JSON.parse(event.data);
          if (message.type==="subscribed") {attempt=0;useMarketStore.getState().setConnection("connected");return;}
          if (message.type==="heartbeat") return;
          if (message.type==="unavailable") {
            if (typeof message.symbol==="string" && typeof message.channel==="string") useMarketStore.getState().clear(message.symbol,message.channel);
            else ws.close();
            return;
          }
          const result=SnapshotSchema.safeParse(message);
          if (!result.success) {ws.close();return;}
          useMarketStore.getState().apply(result.data);
        } catch {ws.close();}
      };
      ws.onerror=() => ws.close();
      ws.onclose=() => {
        if (disposed || socket!==ws) return;
        useMarketStore.getState().setConnection("retrying");
        attempt++;timer=setTimeout(connect,Math.min(30000,1000*2**Math.min(attempt,5)));
      };
    }
    connect();
    return () => {disposed=true;if(timer)clearTimeout(timer);socket?.close();useMarketStore.getState().setConnection("offline");};
  },[timeframe]);
  return null;
}
