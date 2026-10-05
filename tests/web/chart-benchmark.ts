// Synthetic benchmark only: never injected into the product's market store.
import {createChartAdapter,type ChartBar} from "../../apps/web/lib/chart-adapter";
export async function benchmark(count=100000) {
  const element=document.createElement("div");element.style.cssText="width:1000px;height:600px";document.body.append(element);
  const bars:ChartBar[]=Array.from({length:count},(_,i)=>{const open=100+(i%60)*.01;return {open_time:new Date(Date.UTC(2020,0,1)+i*60000).toISOString(),open:String(open),high:String(open+.2),low:String(open-.2),close:String(open+.05),volume:"2"};});
  const adapter=createChartAdapter(element);
  const started=performance.now();adapter.setHistory(bars);const historyMs=performance.now()-started;
  await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
  const updates=[];
  for(let i=0;i<100;i++){const begin=performance.now();adapter.update({...bars[bars.length-1],close:"100.10"});updates.push(performance.now()-begin);}
  adapter.dispose();element.remove();updates.sort((a,b)=>a-b);
  return {bars:count,historyMs,updateP95Ms:updates[94],updateMaxMs:updates[99]};
}
