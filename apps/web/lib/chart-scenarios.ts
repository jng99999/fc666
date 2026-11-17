import type {PriceBar} from './price-action';

export type Direction = 'LONG'|'SHORT';
export type ChartReferenceLine = {id:string;label:string;price:number;color:string};
export type TradingCosts = {feeRate:number;spread:number;slippage:number;fundingPerDay:number};
export type PlanLeg = {direction:Direction;quantity:number;entry:number;stop:number;target:number};
export type PlanResult = {entryFill:number;stopFill:number;targetFill:number;risk:number;reward:number;ratio:number|null;breakEven:number;roundTripCost:number};
const finite=(value:number)=>Number.isFinite(value);
export function validateCosts(costs:TradingCosts):void {
 if(![costs.feeRate,costs.spread,costs.slippage,costs.fundingPerDay].every(finite)||costs.feeRate<0||costs.feeRate>=1||costs.spread<0||costs.slippage<0||costs.fundingPerDay<0||costs.fundingPerDay>=1)throw new Error('费用须为有限非负数；单边费率、每日资金成本须小于 1。');
}
/** Research price assumptions only; never an order or server account mutation. */
export function calculatePlan(leg:PlanLeg,costs:TradingCosts):PlanResult {
 validateCosts(costs);
 if(![leg.quantity,leg.entry,leg.stop,leg.target].every(value=>finite(value)&&value>0)||!['LONG','SHORT'].includes(leg.direction))throw new Error('数量与价格必须为有限正数。');
 const sign=leg.direction==='LONG'?1:-1;
 if(sign*(leg.entry-leg.stop)<=0||sign*(leg.target-leg.entry)<=0)throw new Error('止损、入场和止盈方向不正确。');
 const impact=costs.spread/2+costs.slippage,entryFill=leg.entry+sign*impact,stopFill=leg.stop-sign*impact,targetFill=leg.target-sign*impact;
 if(Math.min(entryFill,stopFill,targetFill)<=0)throw new Error('点差或滑点超过有效价格范围。');
 const pnl=(exit:number)=>(sign*(exit-entryFill)-costs.feeRate*(entryFill+exit))*leg.quantity;
 const risk=-pnl(stopFill),reward=pnl(targetFill);
 const breakEven=entryFill*(sign+costs.feeRate)/(sign-costs.feeRate)+sign*impact;
 const roundTripCost=(costs.spread+2*costs.slippage+costs.feeRate*(entryFill+targetFill))*leg.quantity;
 if(![risk,reward,breakEven,roundTripCost].every(finite))throw new Error('价格或数量超出研究计算的有效范围。');
 return {entryFill,stopFill,targetFill,risk,reward,ratio:risk>0?reward/risk:null,breakEven,roundTripCost};
}

export const STUDIES=[['range','箱内反弹'],['retest','Donchian 突破回踩'],['false-break','假突破收回'],['pullback','趋势回撤']] as const;
export type StudyKind=typeof STUDIES[number][0];
export type StudyTrade={signalAt:number;entryAt:number;exitAt:number;direction:Direction;entry:number;exit:number;net:number;cost:number;ambiguous:boolean;sample:'早期'|'后期';reason:string};
export type StudyResult={kind:StudyKind;label:string;trades:StudyTrade[];net:number;returnPercent:number;maxDrawdown:number;cost:number;ambiguous:number;earlyNet:number;lateNet:number};
export type StudyReport={results:StudyResult[];from:number;to:number;splitAt:number;bars:number;excluded:number;warnings:string[]};
export type StudyOptions={costs:TradingCosts;quantity:number;capital:number;now:number};

function usable(bar:PriceBar):boolean {return [bar.time,bar.closedAt,bar.open,bar.high,bar.low,bar.close].every(finite)&&bar.closedAt>bar.time&&bar.low>0&&bar.low<=Math.min(bar.open,bar.close)&&bar.high>=Math.max(bar.open,bar.close);}
/** A fixed-quantity, causal replay. Indicators use only bars closed before each signal. */
export function runChartStudies(input:readonly PriceBar[],options:StudyOptions):StudyReport {
 validateCosts(options.costs);
 if(!finite(options.now)||!finite(options.quantity)||options.quantity<=0||!finite(options.capital)||options.capital<=0)throw new Error('研究数量、资金和截止时间无效。');
 // Reject a broken sequence rather than silently reconnecting candles across missing history.
 const closed=input.filter(bar=>bar.closedAt<=options.now).slice(-500);
 if(closed.some((bar,index)=>!usable(bar)||(index>0&&(bar.time<=closed[index-1].time||bar.time<closed[index-1].closedAt))))throw new Error('历史柱包含无效价格、重复或乱序时间，无法研究。');
 if(closed.length<60)throw new Error('至少需要 60 根有效闭合 K 线；当前历史不足。');
 const bars=closed,split=Math.floor(bars.length*.7),splitAt=bars[split].time;
 const typical=bars.map(bar=>bar.closedAt-bar.time).sort((a,b)=>a-b)[Math.floor(bars.length/2)];
 const gaps=bars.slice(1).filter((bar,index)=>bar.time-bars[index].closedAt>typical*.1).length;
 const warnings=['固定数量、无参数寻优；后期 30% 仅作时间留出检验，不是独立市场或长期验证。','价格来自 OHLC：无法还原逐笔成交；同柱止盈止损一律先计止损，跳空按更不利开盘价。','未建模消息禁入、交易时段、借币、保证金或强平；研究空头仅为合成模拟，不代表现货可卖空。'];
 if(gaps)warnings.push(`检测到 ${gaps} 处时间缺口：持仓在缺口后首个开盘强制退出，不跨缺口生成入场。`);
 const results=STUDIES.map(([kind,label]):StudyResult=>{
  const trades:StudyTrade[]=[];
  let active:{direction:Direction;entry:number;stop:number;target:number;entryAt:number;signalAt:number;sample:'早期'|'后期'}|null=null;
  let armed:{direction:Direction;level:number;expires:number}|null=null;
  let segmentStart=0;
  for(let index=1;index<40;index++)if(bars[index].time-bars[index-1].closedAt>typical*.1)segmentStart=index;
  const impact=options.costs.spread/2+options.costs.slippage;
  const close=(bar:PriceBar,raw:number,ambiguous:boolean,reason:string)=>{
   if(!active)return;
   const sign=active.direction==='LONG'?1:-1,exit=raw-sign*impact;
   if(exit<=0)throw new Error('点差或滑点超过历史有效价格范围。');
   const days=Math.max(0,(bar.closedAt-active.entryAt)/86400000);
   const fee=(active.entry+exit)*options.quantity*options.costs.feeRate;
   const funding=active.entry*options.quantity*options.costs.fundingPerDay*days;
   const cost=fee+funding+2*impact*options.quantity;
   trades.push({signalAt:active.signalAt,entryAt:active.entryAt,exitAt:bar.closedAt,direction:active.direction,entry:active.entry,exit,net:sign*(exit-active.entry)*options.quantity-fee-funding,cost,ambiguous,sample:active.sample,reason});active=null;
  };
  for(let i=40;i<bars.length;i++){
   const bar=bars[i],prior=bars[i-1],gap=bar.time-prior.closedAt>typical*.1,boundary=i===split;
   if(gap){segmentStart=i;armed=null;}
   if(active&&(gap||boundary)){close({...bar,closedAt:bar.time},bar.open,false,gap?'缺口退出':'时间留出边界退出');armed=null;}
   if(!active&&!gap&&!boundary&&i-segmentStart>=40){
    const history=bars.slice(i-21,i-1),upper=Math.max(...history.map(row=>row.high)),lower=Math.min(...history.map(row=>row.low));
    const ranges=bars.slice(i-15,i-1).map((row,j)=>Math.max(row.high-row.low,Math.abs(row.high-bars[i-16+j].close),Math.abs(row.low-bars[i-16+j].close)));
    const atr=ranges.reduce((a,b)=>a+b,0)/ranges.length;
    const slow=bars.slice(i-40,i-1),mean=slow.reduce((a,b)=>a+b.close,0)/slow.length;
    const fast=bars.slice(i-11,i-1).reduce((a,b)=>a+b.close,0)/10;
    let direction:Direction|null=null;
    if(atr>0&&upper-lower>atr){
     if(kind==='range'){
      if(upper-lower<atr*8&&prior.low<=lower+atr*.3&&prior.close>lower+atr*.3&&prior.close<lower+(upper-lower)*.4)direction='LONG';
      if(upper-lower<atr*8&&prior.high>=upper-atr*.3&&prior.close<upper-atr*.3&&prior.close>lower+(upper-lower)*.6)direction='SHORT';
     }
     if(kind==='false-break'){
      if(prior.low<lower&&prior.close>lower)direction='LONG';
      if(prior.high>upper&&prior.close<upper)direction='SHORT';
     }
     if(kind==='pullback'){
      if(fast>mean&&prior.low<=fast&&prior.close>fast&&prior.close>prior.open)direction='LONG';
      if(fast<mean&&prior.high>=fast&&prior.close<fast&&prior.close<prior.open)direction='SHORT';
     }
     if(kind==='retest'){
      if(armed&&i<=armed.expires&&((armed.direction==='LONG'&&prior.low<=armed.level&&prior.close>armed.level)||(armed.direction==='SHORT'&&prior.high>=armed.level&&prior.close<armed.level))){direction=armed.direction;armed=null;}
      else if(prior.close>upper)armed={direction:'LONG',level:upper,expires:i+5};
      else if(prior.close<lower)armed={direction:'SHORT',level:lower,expires:i+5};
      if(armed&&i>armed.expires)armed=null;
     }
     if(direction){
      const sign=direction==='LONG'?1:-1,entry=bar.open+sign*impact,stop=bar.open-sign*atr*1.5,target=bar.open+sign*atr*3;
      // Fixed collateral budget, never use fees/PnL to change later position sizing.
      if(Math.min(entry,stop,target)>0&&entry*options.quantity<=options.capital)active={direction,entry,stop,target,entryAt:bar.time,signalAt:prior.closedAt,sample:i<split?'早期':'后期'};
     }
    }
   }
   if(active){
    const long=active.direction==='LONG',stopped=long?bar.low<=active.stop:bar.high>=active.stop,won=long?bar.high>=active.target:bar.low<=active.target;
    if(stopped)close(bar,long?Math.min(active.stop,bar.open):Math.max(active.stop,bar.open),won,'止损');
    else if(won)close(bar,active.target,false,'止盈');
    else if(i===bars.length-1)close(bar,bar.close,false,'样本结束估值退出');
   }
  }
  let equity=options.capital,peak=equity,drawdown=0;
  for(const trade of trades){equity+=trade.net;peak=Math.max(peak,equity);drawdown=Math.max(drawdown,peak-equity);}
  const net=trades.reduce((sum,row)=>sum+row.net,0);
  return {kind,label,trades,net,returnPercent:net/options.capital*100,maxDrawdown:drawdown,cost:trades.reduce((sum,row)=>sum+row.cost,0),ambiguous:trades.filter(row=>row.ambiguous).length,earlyNet:trades.filter(row=>row.sample==='早期').reduce((sum,row)=>sum+row.net,0),lateNet:trades.filter(row=>row.sample==='后期').reduce((sum,row)=>sum+row.net,0)};
 });
 return {results,from:bars[0].time,to:bars.at(-1)!.closedAt,splitAt,bars:bars.length,excluded:input.length-bars.length,warnings};
}

export type ExposurePosition={direction:Direction;quantity:number;entry:number;equity:number;mark:number};
/** Same-instrument/currency only; caller must not combine USD gold and USDT crypto. */
export function summarizeExposure(positions:readonly ExposurePosition[],shockPercent:number){
 if(!finite(shockPercent)||shockPercent<=-100||positions.some(row=>!['LONG','SHORT'].includes(row.direction)||![row.quantity,row.entry,row.equity,row.mark].every(finite)||row.quantity<0||row.entry<=0||row.mark<=0))throw new Error('持仓或压力幅度无效。');
 let netQuantity=0,grossNotional=0,equity=0,shockPnl=0,longQuantity=0,shortQuantity=0,longCost=0,shortCost=0;
 for(const row of positions){const sign=row.direction==='LONG'?1:-1;netQuantity+=sign*row.quantity;grossNotional+=row.quantity*row.mark;equity+=row.equity;shockPnl+=sign*row.quantity*row.mark*shockPercent/100;if(sign===1){longQuantity+=row.quantity;longCost+=row.quantity*row.entry;}else{shortQuantity+=row.quantity;shortCost+=row.quantity*row.entry;}}
 if(![netQuantity,grossNotional,equity,shockPnl,longCost,shortCost,equity+shockPnl].every(finite))throw new Error('持仓金额超出研究计算的有效范围。');
 return {netQuantity,grossNotional,equity,shockPnl,stressedEquity:equity+shockPnl,longAverage:longQuantity?longCost/longQuantity:null,shortAverage:shortQuantity?shortCost/shortQuantity:null,netBreakEven:Math.abs(netQuantity)>1e-10?(longCost-shortCost)/netQuantity:null};
}
