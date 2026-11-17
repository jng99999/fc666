import type {PriceAnalysis,PriceBar} from './price-action';

export type ChartPattern={id:string;kind:'DOUBLE_TOP'|'DOUBLE_BOTTOM';direction:'down'|'up';formedAt:number;breakAt:number|null;through:number;neckline:number;invalidation:number;target:number;buffer:number;state:'CANDIDATE'|'CONFIRMED'|'RETESTED'|'FAILED'|'TARGET_REACHED'|'AMBIGUOUS'|'EXPIRED';points:{time:number;price:number}[]};
export const chartPatternStates={CANDIDATE:'等待颈线收盘突破',CONFIRMED:'颈线收盘突破已确认',RETESTED:'突破后回抽已确认',FAILED:'形态失效',TARGET_REACHED:'测算目标已触及',AMBIGUOUS:'同柱冲突，顺序未知',EXPIRED:'历史缺口后已过期'} as const;

/** Research definition, not a TradingView clone or an execution signal. Uses only confirmed 2/2 pivots. */
export function chartPatterns(bars:readonly PriceBar[],analysis:PriceAnalysis):ChartPattern[]{
 if(bars.length<7||analysis.confirmedThrough===null)return [];
 const pivots=analysis.annotations.filter(a=>a.rule==='confirmed-pivot-band-v1'&&a.confirmedAt<=bars.at(-1)!.closedAt).sort((a,b)=>a.start-b.start);
 const indexByTime=new Map(bars.map((bar,i)=>[bar.time,i])),results:ChartPattern[]=[];
 for(let i=2;i<pivots.length;i++){
  const triple=pivots.slice(i-2,i+1),[a,n,b]=triple,top=a.kind==='resistance';
  if(a.kind!==b.kind||a.kind===n.kind||a.start>=n.start||n.start>=b.start)continue;
  const first=indexByTime.get(a.start),last=indexByTime.get(b.start);if(first===undefined||last===undefined||last-first<4||last-first>80)continue;
  const points=triple.map(p=>({time:p.start,price:(p.low+p.high)/2})),[p,neck,q]=points;
  const neckline=neck.price,height=top?(p.price+q.price)/2-neckline:neckline-(p.price+q.price)/2;
  // Peaks must differ by at most 10% of pattern height; depth must exceed 0.3% of the neckline.
  if(height<=neckline*.003||Math.abs(p.price-q.price)>height*.1)continue;
  const buffer=Math.max(...triple.map(p=>(p.high-p.low)/2)),formedAt=Math.max(...triple.map(p=>p.confirmedAt));
  const formation=bars.findIndex(bar=>bar.closedAt===formedAt);if(formation<last||bars.slice(first,formation+1).some((bar,j)=>j>0&&bar.time!==bars[first+j-1].closedAt))continue;
  // A reversal needs an actual approach into the first peak/trough, not just two similar prices.
  const approach=bars.slice(Math.max(0,first-10),first);if(approach.length<4||approach.some((bar,j)=>j>0&&bar.time!==approach[j-1].closedAt)||bars[first].time!==approach.at(-1)!.closedAt)continue;
  const runup=top?p.price-Math.min(...approach.map(bar=>bar.low)):Math.max(...approach.map(bar=>bar.high))-p.price;
  if(runup<height*.5)continue;
  const invalidation=top?Math.max(p.price,q.price)+buffer:Math.min(p.price,q.price)-buffer,target=top?neckline-height:neckline+height;
  if(target<=0)continue;
  const result:ChartPattern={id:`${top?'M':'W'}:${a.start}:${n.start}:${b.start}`,kind:top?'DOUBLE_TOP':'DOUBLE_BOTTOM',direction:top?'down':'up',formedAt,breakAt:null,through:bars[formation].time,neckline,invalidation,target,buffer,state:'CANDIDATE',points};
  for(let j=formation;j<bars.length;j++){
   const bar=bars[j];
   if(j>formation&&bar.time!==bars[j-1].closedAt){result.state='EXPIRED';break;}
   result.through=bar.time;
   const invalid=top?bar.close>invalidation:bar.close<invalidation;
   if(result.breakAt===null){if(invalid){result.state='FAILED';break;}if(top?bar.close<neckline-buffer:bar.close>neckline+buffer){result.state='CONFIRMED';result.breakAt=bar.closedAt;}continue;}
   // Do not score an intrabar target on the closing-confirmation candle itself.
   const hit=top?bar.low<=target:bar.high>=target;
   const crossedInvalidation=top?bar.high>=invalidation:bar.low<=invalidation;
   if(hit&&crossedInvalidation){result.state='AMBIGUOUS';break;}
   if(invalid||(top?bar.close>neckline+buffer:bar.close<neckline-buffer)){result.state='FAILED';break;}
   if(hit){result.state='TARGET_REACHED';break;}
   if(top?bar.high>=neckline-buffer&&bar.close<neckline-buffer:bar.low<=neckline+buffer&&bar.close>neckline+buffer)result.state='RETESTED';
  }
  results.push(result);
 }
 // Keep the latest observed pattern of either direction, including failure; never conceal a failed result.
 return results.sort((a,b)=>b.formedAt-a.formedAt).slice(0,1);
}
