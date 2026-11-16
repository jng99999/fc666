/** Causal chart research only. Floating point values never update an account ledger. */
export type PriceBar = {time:number;closedAt:number;open:number;high:number;low:number;close:number;volume:number|null};
export type PriceContext = {instrumentId:string;source:string;timeframe:string;volumeKind:'trade'|'tick'|'unavailable'};
export type PriceAnnotation = {
 id:string;kind:'support'|'resistance'|'range'|'trend'|'channel'|'demand'|'supply'|'fvg'|'bos'|'choch'|'session'|'level'|'fibonacci'|'liquidity';
 label:string;low:number;high:number;start:number;end:number;confirmedAt:number;direction:'up'|'down'|'neutral';
 state:'ACTIVE'|'TOUCHED'|'MITIGATED'|'BROKEN'|'EXPIRED';touches:number;explanation:string;rule:string;points?:{time:number;price:number}[];
};
export type PriceAnalysis = {
 annotations:PriceAnnotation[];regime:'UP'|'DOWN'|'RANGE'|'UNAVAILABLE';atr:number|null;last:number|null;confirmedThrough:number|null;warnings:string[];
 volumeProfile:{kind:'OHLCV_ESTIMATE'|'UNAVAILABLE';bins:{low:number;high:number;volume:number}[];poc:number|null;vah:number|null;val:number|null;totalVolume:number;reason:string};
};

export const PRICE_ACTION_RULE_VERSION='closed-price-structure-v1';
const minute=60000,day=86400000,radius=2,boxWindow=24,atrPeriod=14;
const intervals:Record<string,number>={'1m':minute,'5m':5*minute,'15m':15*minute,'30m':30*minute,'1h':60*minute,'4h':240*minute,'1d':day};
type Pivot={kind:'high'|'low';time:number;confirmedAt:number;price:number;index:number};
type LineState={slope:number;originTime:number;originPrice:number;offset:number;side:'support'|'resistance'};
type Lifetime={through?:number;line?:LineState;touched?:boolean;brokenAt?:number;breakDirection?:'up'|'down';retested?:boolean;failed?:boolean;buffer:number};
type PeriodGroup={start:number;end:number;first:number;last:number;high:number;low:number;open:number;count:number;complete:boolean};
const active=(item:PriceAnnotation)=>item.state==='ACTIVE'||item.state==='TOUCHED';
const average=(values:number[])=>values.reduce((sum,value)=>sum+value,0)/values.length;
const tolerance=(price:number,atr:number|null)=>Math.max(price*1e-8,(atr??0)*.15);
const utcDay=(time:number)=>Math.floor(time/day)*day;
const utcWeek=(time:number)=>{const today=utcDay(time),weekday=new Date(today).getUTCDay();return today-((weekday+6)%7)*day;};

/** Closed means the caller supplied only final bars. No wall-clock is read, so replay stays reproducible. */
export function validatePriceBars(bars:readonly PriceBar[],context:PriceContext):void {
 if(!Array.isArray(bars)||bars.length>5000)throw new Error('结构分析只接受最多 5000 根闭合柱。');
 if(!context||![context.instrumentId,context.source,context.timeframe].every(value=>typeof value==='string'&&value.length>0&&value.length<=256)||!['trade','tick','unavailable'].includes(context.volumeKind))throw new Error('结构分析需要明确的产品、来源、周期和成交量口径。');
 const parsed=/^([1-9]\d*)(m|h)$/.exec(context.timeframe);
 const interval=intervals[context.timeframe]??(parsed?Number(parsed[1])*(parsed[2]==='m'?minute:60*minute):undefined);
 for(let i=0;i<bars.length;i++){
  const b=bars[i];
  if(!b||![b.time,b.closedAt].every(value=>Number.isSafeInteger(value)&&value>=0&&value<=8640000000000000)||b.closedAt<=b.time||interval!==undefined&&b.closedAt-b.time!==interval||![b.open,b.high,b.low,b.close].every(value=>Number.isFinite(value)&&value>0)||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close)||b.volume!==null&&(!Number.isFinite(b.volume)||b.volume<0))throw new Error('结构分析拒绝时间、价格或成交量无效的 K 线。');
  if(i&&b.time<bars[i-1].closedAt)throw new Error('结构分析拒绝重复、乱序或重叠的 K 线。');
 }
}

function unavailableProfile(reason:string):PriceAnalysis['volumeProfile']{
 return {kind:'UNAVAILABLE',bins:[],poc:null,vah:null,val:null,totalVolume:0,reason};
}

/** Uniformly allocates each candle's volume to intersected price bins. It is never aggressor delta. */
function volumeProfile(bars:readonly PriceBar[],context:PriceContext):PriceAnalysis['volumeProfile']{
 if(context.volumeKind!=='trade')return unavailableProfile(context.volumeKind==='tick'?'只有报价次数，不能冒充真实成交量分布。':'本来源未提供真实成交量，成交量分布不可用。');
 const sample=bars.slice(-256);
 if(!sample.length||sample.some(bar=>bar.volume===null))return unavailableProfile('已加载闭合柱存在成交量缺失，未用 0 或其他市场补齐。');
 const total=sample.reduce((sum,bar)=>sum+(bar.volume??0),0),low=Math.min(...sample.map(b=>b.low)),high=Math.max(...sample.map(b=>b.high));
 if(!Number.isFinite(total)||total<=0)return unavailableProfile('本窗口没有可用的正成交量。');
 const count=32,spread=Math.max(high-low,Math.abs(low)*1e-8),step=spread/count;
 const bins=Array.from({length:count},(_,i)=>({low:low+i*step,high:low+(i+1)*step,volume:0}));
 for(const b of sample){
  if(b.high===b.low){bins[Math.min(count-1,Math.floor((b.close-low)/step))].volume+=b.volume!;continue;}
  for(const bin of bins){const overlap=Math.max(0,Math.min(bin.high,b.high)-Math.max(bin.low,b.low));if(overlap)bin.volume+=b.volume!*overlap/(b.high-b.low);}
 }
 let peak=0;
 bins.forEach((bin,i)=>{if(bin.volume>bins[peak].volume)peak=i;});
 let first=peak,last=peak,captured=bins[peak].volume;
 while(captured<total*.7&&(first>0||last<count-1)){
  if(first>0&&(last===count-1||bins[first-1].volume>=bins[last+1].volume))captured+=bins[--first].volume;
  else captured+=bins[++last].volume;
 }
 return {kind:'OHLCV_ESTIMATE',bins,poc:(bins[peak].low+bins[peak].high)/2,vah:bins[last].high,val:bins[first].low,totalVolume:total,reason:`最近 ${sample.length} 根已加载闭合柱的 OHLCV 近似分布；70% 价值区域，不是逐笔成交或主动买卖量。`};
}

const zoneKinds=new Set<PriceAnnotation['kind']>(['support','resistance','range','demand','supply','fvg']);
const sessionDefinitions=[
 {name:'东京观察窗口',zone:'Asia/Tokyo',start:9,end:17},
 {name:'伦敦观察窗口',zone:'Europe/London',start:8,end:16},
 {name:'纽约观察窗口',zone:'America/New_York',start:8,end:16},
] as const;
const sessionClocks=sessionDefinitions.map(value=>new Intl.DateTimeFormat('en-CA',{timeZone:value.zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}));
function clock(time:number,index:number){
 const parts=Object.fromEntries(sessionClocks[index].formatToParts(time).map(value=>[value.type,value.value]));
 return {date:`${parts.year}-${parts.month}-${parts.day}`,hour:Number(parts.hour),minute:Number(parts.minute),exactHour:Number(parts.minute)===0&&Number(parts.second)===0&&time%1000===0};
}

/** Rebuilds the same deterministic event stream for each supplied prefix; never reads future bars. */
export function analyzePriceAction(bars:readonly PriceBar[],context:PriceContext):PriceAnalysis {
 validatePriceBars(bars,context);
 const annotations:PriceAnnotation[]=[],ids=new Set<string>(),lifetime=new Map<string,Lifetime>(),watching=new Set<PriceAnnotation>(),warnings:string[]=[];
 const scope=JSON.stringify([context.instrumentId,context.source,context.timeframe]);
 let segmentStart=0,atr:number|null=null,seed:number[]=[],pivots:Pivot[]=[],lastHigh:Pivot|undefined,lastLow:Pivot|undefined;
 let highBroken=false,lowBroken=false,structure:'up'|'down'|null=null,range:PriceAnnotation|undefined,lastRangeBreak=-Infinity;
 let dayGroup:PeriodGroup|undefined,weekGroup:PeriodGroup|undefined;
 const sessionGroups=new Map<string,PeriodGroup>(),swept=new Set<string>();
 const add=(value:Omit<PriceAnnotation,'id'|'state'|'touches'|'rule'>&{rule:string;touches?:number},life:Lifetime={buffer:0})=>{
  const id=JSON.stringify([scope,value.kind,value.rule,value.start,value.confirmedAt,value.low,value.high]);
  if(ids.has(id))return undefined;
  const item:PriceAnnotation={...value,id,state:'ACTIVE',touches:value.touches??0};
  annotations.push(item);ids.add(id);lifetime.set(id,life);
  if(zoneKinds.has(item.kind)||life.line||life.through!==undefined||item.rule==='equal-pivots-v1')watching.add(item);
  return item;
 };
 const periodLine=(label:string,price:number,start:number,confirmedAt:number,through:number,rule:string)=>add({kind:'level',label,low:price,high:price,start,end:start,confirmedAt,direction:'neutral',explanation:'只使用完整覆盖的已结束 UTC 周期；当前周期不提供未来高低点。',rule},{through,buffer:0});
 const isComplete=(group:PeriodGroup|undefined)=>group?.complete&&group.first===group.start&&group.last===group.end;
 const updatePeriod=(previous:PeriodGroup|undefined,start:number,end:number,b:PriceBar)=>{
  if(!previous||previous.start!==start)return {start,end,first:b.time,last:b.closedAt,high:b.high,low:b.low,open:b.open,count:1,complete:b.time===start};
  previous.complete=previous.complete&&previous.last===b.time;previous.last=b.closedAt;previous.high=Math.max(previous.high,b.high);previous.low=Math.min(previous.low,b.low);previous.count++;return previous;
 };

 for(let index=0;index<bars.length;index++){
  const b=bars[index],prev=bars[index-1];
  if(prev&&b.time!==prev.closedAt){
   if(!warnings.includes('历史存在缺口：结构在缺口处分段，未跨缺口连接箱体、趋势或缺口形态。'))warnings.push('历史存在缺口：结构在缺口处分段，未跨缺口连接箱体、趋势或缺口形态。');
   for(const item of annotations)if(active(item))item.state='EXPIRED';
   watching.clear();
   segmentStart=index;atr=null;seed=[];pivots=[];lastHigh=lastLow=undefined;highBroken=lowBroken=false;structure=null;range=undefined;lastRangeBreak=-Infinity;
  }
  const previous=index>segmentStart?prev:undefined;
  const tr=previous?Math.max(b.high-b.low,Math.abs(b.high-previous.close),Math.abs(b.low-previous.close)):b.high-b.low;
  if(atr===null){seed.push(tr);if(seed.length===atrPeriod)atr=average(seed);}else atr=(atr*(atrPeriod-1)+tr)/atrPeriod;
  const tol=tolerance(b.close,atr);

  // Only older objects observe this bar; newly created zones cannot self-mitigate.
  for(const item of [...watching]){
   const life=lifetime.get(item.id)!;
   if(!active(item)&&(life.brokenAt===undefined||index-life.brokenAt>8)){watching.delete(item);continue;}
   if(item.kind==='range'&&life.brokenAt!==undefined&&index-life.brokenAt<=8){
    const back=b.close>item.low&&b.close<item.high;
    if(back&&!life.failed){life.failed=true;add({kind:'liquidity',label:'箱体破位后收回',low:item.low,high:item.high,start:b.time,end:b.time,confirmedAt:b.closedAt,direction:life.breakDirection==='up'?'down':'up',explanation:'已确认箱体破位后八柱内收回原区间；保留原破位，不移动旧边界。这是价格事件，不证明真实止损成交。',rule:'range-return-v1'});}
    const retest=life.breakDirection==='up'?b.low<=item.high+life.buffer&&b.close>item.high:b.high>=item.low-life.buffer&&b.close<item.low;
    if(retest&&!back&&!life.retested&&index>life.brokenAt){life.retested=true;const level=life.breakDirection==='up'?item.high:item.low;add({kind:'level',label:'箱体边界回踩候选',low:level,high:level,start:b.time,end:b.time,confirmedAt:b.closedAt,direction:life.breakDirection!,explanation:'价格在破位之后另一次闭合柱回访边界并收于箱外；这是研究候选，不是成交或买卖指令。',rule:'range-retest-v1'});}
   }
   if(!active(item)||item.confirmedAt>=b.closedAt)continue;
   if(life.through!==undefined){
    if(b.time>=life.through)item.state='EXPIRED';else item.end=b.time;
    continue;
   }
   if(life.line){
    const line=life.line,projected=line.originPrice+line.slope*(b.time-line.originTime);
    const second=projected+line.offset;
    const broken=item.kind==='channel'?b.close<Math.min(projected,second)-life.buffer||b.close>Math.max(projected,second)+life.buffer:line.side==='support'?b.close<projected-life.buffer:b.close>projected+life.buffer;
    if(broken)item.state='BROKEN';
    else if(Math.abs((line.side==='support'?b.low:b.high)-projected)<=life.buffer){item.state='TOUCHED';item.touches++;}
    item.end=b.time;continue;
   }
   const liquidityPool=item.rule==='equal-pivots-v1';
   if(!zoneKinds.has(item.kind)&&!liquidityPool)continue;
   const inside=b.high>=item.low&&b.low<=item.high;
   if(inside&&!life.touched){item.touches++;item.state='TOUCHED';}
   life.touched=inside;item.end=b.time;
   if(item.kind==='fvg'){
    if(item.direction==='up'?b.low<=item.low:b.high>=item.high)item.state='MITIGATED';
   }else{
    const broken=item.kind==='range'||liquidityPool?b.close>item.high+life.buffer||b.close<item.low-life.buffer:item.direction==='up'?b.close<item.low-life.buffer:b.close>item.high+life.buffer;
    if(broken){item.state='BROKEN';if(item.kind==='range'){life.brokenAt=index;life.breakDirection=b.close>item.high?'up':'down';lastRangeBreak=index;}}
   }
  }

  // Historical period references appear only when complete preceding coverage is proven.
  const today=utcDay(b.time),week=utcWeek(b.time);
  if(dayGroup&&dayGroup.start!==today&&isComplete(dayGroup)&&dayGroup.end===today){
   periodLine('前日高点 · UTC',dayGroup.high,b.time,b.closedAt,today+day,'previous-day-high-v1');
   periodLine('前日低点 · UTC',dayGroup.low,b.time,b.closedAt,today+day,'previous-day-low-v1');
  }
  if(weekGroup&&weekGroup.start!==week&&isComplete(weekGroup)&&weekGroup.end===week){
   periodLine('前周高点 · UTC',weekGroup.high,b.time,b.closedAt,week+7*day,'previous-week-high-v1');
   periodLine('前周低点 · UTC',weekGroup.low,b.time,b.closedAt,week+7*day,'previous-week-low-v1');
  }
  if(b.time===today)periodLine('当日开盘 · UTC',b.open,b.time,b.closedAt,today+day,'utc-day-open-v1');
  dayGroup=updatePeriod(dayGroup,today,today+day,b);weekGroup=updatePeriod(weekGroup,week,week+7*day,b);
  sessionDefinitions.forEach((session,sessionIndex)=>{
   const opening=clock(b.time,sessionIndex),closing=clock(b.closedAt,sessionIndex);
   if(opening.hour<session.start||opening.hour>=session.end)return;
   const key=JSON.stringify([sessionIndex,opening.date]),group=sessionGroups.get(key);
   const next:PeriodGroup=group?{...group,last:b.closedAt,high:Math.max(group.high,b.high),low:Math.min(group.low,b.low),count:group.count+1,complete:group.complete&&group.last===b.time}:{start:b.time,end:b.closedAt,first:b.time,last:b.closedAt,high:b.high,low:b.low,open:b.open,count:1,complete:opening.hour===session.start&&opening.exactHour};
   sessionGroups.set(key,next);
   if(next.complete&&closing.date===opening.date&&closing.hour===session.end&&closing.exactHour)add({kind:'session',label:`${session.name} · 已完成`,low:next.low,high:next.high,start:next.start,end:b.time,confirmedAt:b.closedAt,direction:'neutral',explanation:`${session.zone} ${session.start}:00–${session.end}:00 观察窗口，已闭合完整覆盖；是研究窗口，不是所有市场的官方开收盘时间。`,rule:`session-${session.zone}-v1`});
  });

  const structureBreak=(pivot:Pivot,direction:'up'|'down')=>{
   const kind=structure!==null&&structure!==direction?'choch':'bos';structure=direction;
   add({kind,label:`${kind==='bos'?'BOS 结构突破':'CHoCH 结构转变'} ${direction==='up'?'↑':'↓'}`,low:pivot.price,high:pivot.price,start:pivot.time,end:b.time,confirmedAt:b.closedAt,direction,points:[{time:pivot.time,price:pivot.price},{time:b.time,price:pivot.price}],explanation:'闭合价格穿越此前已确认且未突破的拐点；首个方向为 BOS，方向改变为 CHoCH。本规则是一种定义，不代表买卖指令。',rule:'closed-structure-break-v1'});
   const origin=bars.slice(Math.max(segmentStart,index-8),index).reverse().find(item=>direction==='up'?item.close<item.open:item.close>item.open);
   if(origin){const low=direction==='up'?origin.low:Math.min(origin.open,origin.close),high=direction==='up'?Math.max(origin.open,origin.close):origin.high;if(high>low)add({kind:direction==='up'?'demand':'supply',label:direction==='up'?'需求 / OB 候选':'供应 / OB 候选',low,high,start:origin.time,end:b.time,confirmedAt:b.closedAt,direction,explanation:'本规则选取结构突破前八柱内最近的反向柱及其影线形成区域；公开价格形态不能证明机构挂单或持仓。',rule:'pre-break-opposite-zone-v1'},{buffer:tol});}
  };
  if(lastHigh&&!highBroken&&b.close>lastHigh.price){highBroken=true;structureBreak(lastHigh,'up');}
  if(lastLow&&!lowBroken&&b.close<lastLow.price){lowBroken=true;structureBreak(lastLow,'down');}
  for(const pivot of [lastHigh,lastLow]){
   if(!pivot||index-pivot.index>60||swept.has(`${pivot.kind}:${pivot.time}`))continue;
   const sweep=pivot.kind==='high'?b.high>pivot.price+tol&&b.close<pivot.price:b.low<pivot.price-tol&&b.close>pivot.price;
   if(sweep){swept.add(`${pivot.kind}:${pivot.time}`);add({kind:'liquidity',label:pivot.kind==='high'?'前高上方刺破后收回':'前低下方刺破后收回',low:pivot.price,high:pivot.price,start:pivot.time,end:b.time,confirmedAt:b.closedAt,direction:pivot.kind==='high'?'down':'up',explanation:'影线超出已确认拐点并收回，按波动容差筛选；仅表示价格扫回，不证明实际止损或机构成交。',rule:'wick-reclaim-v1'});}
  }

  if(index-segmentStart>=2&&atr!==null){
   const first=bars[index-2];
   if(b.low-first.high>=atr*.2&&b.low>first.high)add({kind:'fvg',label:'上行三柱 FVG',low:first.high,high:b.low,start:first.time,end:b.time,confirmedAt:b.closedAt,direction:'up',explanation:'三根连续闭合柱第一根最高与第三根最低之间的不重叠区域，宽度至少 0.2 ATR；第三根闭合后才可用，回补不是必然。',rule:'three-closed-fvg-v1'},{buffer:0});
   if(first.low-b.high>=atr*.2&&first.low>b.high)add({kind:'fvg',label:'下行三柱 FVG',low:b.high,high:first.low,start:first.time,end:b.time,confirmedAt:b.closedAt,direction:'down',explanation:'三根连续闭合柱第一根最低与第三根最高之间的不重叠区域，宽度至少 0.2 ATR；第三根闭合后才可用，回补不是必然。',rule:'three-closed-fvg-v1'},{buffer:0});
  }

  if(index-segmentStart>=radius*2){
   const candidate=bars[index-radius],neighbours=bars.slice(index-radius*2,index+1).filter((_,i)=>i!==radius);
   for(const kind of ['high','low'] as const){
    const price=candidate[kind],isPivot=kind==='high'?neighbours.every(item=>item.high<price):neighbours.every(item=>item.low>price);
    if(!isPivot)continue;
    const pivot:Pivot={kind,price,index:index-radius,time:candidate.time,confirmedAt:b.closedAt},old=kind==='high'?lastHigh:lastLow;
    const label=old?(kind==='high'?(price>old.price?'HH':price<old.price?'LH':'EH'):(price>old.price?'HL':price<old.price?'LL':'EL')):(kind==='high'?'H':'L');
    add({kind:kind==='high'?'resistance':'support',label:`${label} · ${kind==='high'?'阻力':'支撑'}候选`,low:Math.max(Number.MIN_VALUE,price-tol),high:price+tol,start:candidate.time,end:b.time,confirmedAt:b.closedAt,direction:kind==='high'?'down':'up',touches:1,explanation:'左右各两根闭合柱确认的严格高/低拐点，确认延迟两柱；区域容差取形成时的 0.15 ATR，边界不事后修改。',rule:'confirmed-pivot-band-v1'},{buffer:0});
    if(old&&Math.abs(old.price-price)<=tol)add({kind:'liquidity',label:kind==='high'?'等高区域候选':'等低区域候选',low:Math.min(old.price,price)-tol,high:Math.max(old.price,price)+tol,start:old.time,end:b.time,confirmedAt:b.closedAt,direction:'neutral',touches:2,explanation:'相邻同类确认拐点落入形成时波动容差；只标记相近价格，不推断真实止损数量。',rule:'equal-pivots-v1'},{buffer:0});
    if(old&&kind==='low'&&price>old.price||old&&kind==='high'&&price<old.price){
     const from=old!,slope=(price-from.price)/(candidate.time-from.time),side=kind==='low'?'support':'resistance';
     const projection=(bar:PriceBar)=>from.price+slope*(bar.time-from.time);
     const segment=bars.slice(from.index,index+1),valid=segment.every(bar=>side==='support'?bar.low>=projection(bar)-tol:bar.high<=projection(bar)+tol);
     if(valid){
      const line={slope,originTime:from.time,originPrice:from.price,offset:0,side} as const;
      const points=[{time:from.time,price:from.price},{time:candidate.time,price}];
      add({kind:'trend',label:side==='support'?'上升趋势线候选':'下降趋势线候选',low:Math.min(from.price,price),high:Math.max(from.price,price),start:from.time,end:b.time,confirmedAt:b.closedAt,direction:side==='support'?'up':'down',points,touches:2,explanation:'连接两个同类已确认拐点，并检查已到达价格未显著穿越；两点只是候选，后续触碰和破坏会继续记载。',rule:'confirmed-two-pivot-line-v1'},{line,buffer:tol});
      const offset=side==='support'?Math.max(...segment.map(bar=>bar.high-projection(bar))):Math.min(...segment.map(bar=>bar.low-projection(bar)));
      if(Math.abs(offset)>tol){const parallel=points.map(point=>({time:point.time,price:point.price+offset}));if(parallel.every(point=>point.price>0))add({kind:'channel',label:side==='support'?'上升平行通道候选':'下降平行通道候选',low:Math.min(...points.concat(parallel).map(point=>point.price)),high:Math.max(...points.concat(parallel).map(point=>point.price)),start:from.time,end:b.time,confirmedAt:b.closedAt,direction:side==='support'?'up':'down',points:points.concat(parallel),touches:2,explanation:'以已确认趋势线为一侧，用形成时已到达价格的最大偏离构造平行边界；未来不会重新拟合旧通道。',rule:'fixed-parallel-channel-v1'},{line:{...line,offset},buffer:tol});}
     }
    }
    const opposite=pivots.at(-1);
    if(opposite&&opposite.kind!==kind&&opposite.price!==price){
     for(const ratio of [.382,.5,.618]){
      const level=price+(opposite.price-price)*ratio;
      add({kind:'fibonacci',label:`回撤 ${(ratio*100).toFixed(1)}%`,low:level,high:level,start:opposite.time,end:b.time,confirmedAt:b.closedAt,direction:price>opposite.price?'up':'down',points:[{time:opposite.time,price:level},{time:candidate.time,price:level}],explanation:'从两个已确认且方向相反的波段锚点计算回撤位置；确认前不显示，比例本身没有买卖含义。',rule:'confirmed-swing-retracement-v1'});
     }
    }
    pivots.push(pivot);
    if(kind==='high'){lastHigh=pivot;highBroken=false;}else{lastLow=pivot;lowBroken=false;}
   }
  }

  if(index-segmentStart+1>=boxWindow&&atr!==null&&(!range||!active(range))&&index-lastRangeBreak>=boxWindow){
   const window=bars.slice(index-boxWindow+1,index+1),low=Math.min(...window.map(item=>item.low)),high=Math.max(...window.map(item=>item.high)),height=high-low;
   const distance=window.slice(1).reduce((sum,item,i)=>sum+Math.abs(item.close-window[i].close),0),efficiency=distance?Math.abs(window.at(-1)!.close-window[0].close)/distance:0;
   const edge=Math.max(atr*.2,height*.04),hits=(side:'high'|'low')=>{let count=0,last=-Infinity;window.forEach((bar,i)=>{if(i-last>=3&&(side==='high'?bar.high>=high-edge:bar.low<=low+edge)){count++;last=i;}});return count;};
   const upper=hits('high'),lower=hits('low');
   if(height>0&&height<=atr*6&&efficiency<=.35&&upper>=2&&lower>=2){
    range=add({kind:'range',label:'已确认横盘箱体',low,high,start:window[0].time,end:b.time,confirmedAt:b.closedAt,direction:'neutral',touches:upper+lower,explanation:'24 根连续闭合柱，双侧各至少两次间隔触碰、路径效率不超过 0.35、箱高不超过 6 ATR。边界固定；收盘超出形成时 0.1 ATR 缓冲为破位，破位后等待完整新窗口才重建。参数为研究规则，不是最佳参数声明。',rule:'fixed-range-24-v1'},{buffer:atr*.1});
   }
  }
 }

 const segment=bars.slice(segmentStart),sample=segment.slice(-21),movement=sample.slice(1).reduce((sum,item,i)=>sum+Math.abs(item.close-sample[i].close),0),change=sample.length?sample.at(-1)!.close-sample[0].close:0;
 const regime:PriceAnalysis['regime']=sample.length<21||atr===null?'UNAVAILABLE':!movement||Math.abs(change)/movement<.35?'RANGE':change>0?'UP':'DOWN';
 if(!annotations.some(item=>item.rule==='previous-day-high-v1'&&active(item)))warnings.push('未取得当前所需的完整前日覆盖，不提供前日高低点。');
 if(!annotations.some(item=>item.rule==='previous-week-high-v1'&&active(item)))warnings.push('未取得当前所需的完整前周覆盖，不提供前周高低点。');
 if(context.volumeKind!=='trade')warnings.push('成交量口径不支持真实价量分析；未使用其他市场成交量替代。');
 if(bars.length<atrPeriod)warnings.push('闭合历史不足，ATR 与依赖波动的区域仍在预热。');
 return {annotations,regime,atr,last:bars.at(-1)?.close??null,confirmedThrough:bars.at(-1)?.closedAt??null,warnings,volumeProfile:volumeProfile(segment,context)};
}
