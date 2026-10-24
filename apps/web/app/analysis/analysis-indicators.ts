import type {Candle, IndicatorRow} from '../../lib/market';

export type IndicatorId = 'sma'|'smaSlow'|'ema'|'boll'|'rsi'|'macd'|'atr'|'vwap'|'stoch'|'cci'|'wr'|'roc'|'obv';
export type IndicatorConfig = {id:IndicatorId; visible:boolean; params:Record<string,number>};
type Field = {key:string; label:string; min:number; max:number; step?:number};
export const indicatorCatalog: Record<IndicatorId,{name:string; description:string; color:string; pane:'main'|'sub'; defaults:Record<string,number>; fields:Field[]}> = {
 sma:{name:'短期 MA',description:'收盘价的简单移动平均',color:'#e9a22c',pane:'main',defaults:{period:20},fields:[{key:'period',label:'周期',min:2,max:240}]},
 smaSlow:{name:'长期 MA',description:'较长窗口的趋势参照',color:'#2877ff',pane:'main',defaults:{period:60},fields:[{key:'period',label:'周期',min:2,max:500}]},
 ema:{name:'EMA',description:'以 SMA 初始化的指数移动平均',color:'#0d9e91',pane:'main',defaults:{period:20},fields:[{key:'period',label:'周期',min:2,max:240}]},
 boll:{name:'BOLL',description:'均值 ± 倍数 × 总体标准差',color:'#949dd9',pane:'main',defaults:{period:20,multiplier:2},fields:[{key:'period',label:'周期',min:2,max:240},{key:'multiplier',label:'标准差倍数',min:.5,max:5,step:.1}]},
 rsi:{name:'RSI',description:'Wilder 平滑的相对强弱',color:'#9666ef',pane:'sub',defaults:{period:14},fields:[{key:'period',label:'周期',min:2,max:100}]},
 macd:{name:'MACD',description:'快慢 EMA 差与信号线；柱值不乘 2',color:'#2877ff',pane:'sub',defaults:{fast:12,slow:26,signal:9},fields:[{key:'fast',label:'快线',min:2,max:100},{key:'slow',label:'慢线',min:3,max:200},{key:'signal',label:'信号线',min:2,max:100}]},
 atr:{name:'ATR',description:'Wilder 平滑真实波幅，不年化',color:'#c68a44',pane:'sub',defaults:{period:14},fields:[{key:'period',label:'周期',min:2,max:100}]},
 vwap:{name:'UTC VWAP',description:'已加载的当日典型价量加权；可能是部分会话',color:'#4a9c98',pane:'main',defaults:{},fields:[]},
 stoch:{name:'Stochastic',description:'Fast %K 与其简单均线，不是 KDJ',color:'#4a9c98',pane:'sub',defaults:{period:14,smooth:3},fields:[{key:'period',label:'K 周期',min:2,max:100},{key:'smooth',label:'D 周期',min:2,max:30}]},
 cci:{name:'CCI',description:'典型价相对均值的标准化偏离',color:'#af8350',pane:'sub',defaults:{period:20},fields:[{key:'period',label:'周期',min:2,max:100}]},
 wr:{name:'Williams %R',description:'收盘价在区间中的位置，范围 −100 至 0',color:'#648cb5',pane:'sub',defaults:{period:14},fields:[{key:'period',label:'周期',min:2,max:100}]},
 roc:{name:'ROC',description:'与指定柱数前的收盘价比较',color:'#c08a99',pane:'sub',defaults:{period:12},fields:[{key:'period',label:'周期',min:2,max:100}]},
 obv:{name:'OBV',description:'首柱归零的窗口累计基础币量',color:'#62998b',pane:'sub',defaults:{},fields:[]},
};
export const initialIndicators: IndicatorConfig[] = ['sma','smaSlow','rsi'].map(value=>{
 const id=value as IndicatorId;
 return {id,visible:true,params:{...indicatorCatalog[id].defaults}};
});
export function validateIndicator(id:IndicatorId,params:Record<string,number>):string|null {
 const definition=indicatorCatalog[id];
 for(const field of definition.fields){
  const value=params[field.key];
  if(!Number.isFinite(value)||value<field.min||value>field.max||(!field.step&&!Number.isInteger(value)))return `${field.label}须为 ${field.min}–${field.max} 的${field.step?'有效数值':'整数'}。`;
 }
 if(id==='macd'&&params.fast>=params.slow)return 'MACD 快线周期必须小于慢线周期。';
 return null;
}
const average=(values:number[])=>values[0]+values.reduce((sum,value)=>sum+value-values[0],0)/values.length;
function moving(values:(number|null)[],period:number,exponential=false){
 let previous:number|null=null;
 return values.map((value,index)=>{
  if(value===null){previous=null;return null;}
  const window=values.slice(Math.max(0,index-period+1),index+1);
  if(window.length<period||window.some(v=>v===null))return null;
  const next=exponential&&previous!==null?previous+(value-previous)*2/(period+1):average(window as number[]);
  previous=next;return next;
 });
}
export type IndicatorLine={key:string;label:string;color:string;pane:'main'|'sub';values:(number|null)[];histogram?:boolean;guides?:number[]};
export function calculateIndicator(bars:Candle[],config:IndicatorConfig):IndicatorLine[]{
 const {id,params}=config,definition=indicatorCatalog[id],closes=bars.map(b=>Number(b.close)),period=params.period;
 if(validateIndicator(id,params))throw new Error('指标参数无效');
 const line=(key:string,label:string,values:(number|null)[],extra:Partial<IndicatorLine>={}):IndicatorLine=>({key,label,values,color:definition.color,pane:definition.pane,...extra});
 if(id==='sma'||id==='smaSlow'||id==='ema')return [line(id,`${id==='ema'?'EMA':'MA'}${period}`,moving(closes,period,id==='ema'))];
 if(id==='boll'){
  const middle=moving(closes,period),deviation=middle.map((value,i)=>value===null?null:Math.sqrt(average(closes.slice(i-period+1,i+1).map(v=>(v-value)**2)))*params.multiplier);
  return [line('boll-middle',`BOLL(${period},${params.multiplier}) 中`,middle),line('boll-upper','上轨',middle.map((v,i)=>v===null?null:v+deviation[i]!),{color:'#949dd9'}),line('boll-lower','下轨',middle.map((v,i)=>v===null?null:v-deviation[i]!),{color:'#949dd9'})];
 }
 if(id==='macd'){
  const fast=moving(closes,params.fast,true),slow=moving(closes,params.slow,true),dif=closes.map((_,i)=>fast[i]===null||slow[i]===null?null:fast[i]!-slow[i]!);
  const signal=moving(dif,params.signal,true),hist=dif.map((v,i)=>v===null||signal[i]===null?null:v-signal[i]!);
  return [line('macd-histogram',`MACD(${params.fast},${params.slow},${params.signal})`,hist,{histogram:true}),line('macd-dif','DIF',dif,{color:'#2877ff',guides:[0]}),line('macd-signal','DEA',signal,{color:'#e9a22c'})];
 }
 if(id==='rsi'){
  let gain=0,loss=0;
  const values=closes.map((value,i)=>{
   if(i===0)return null;
   const delta=value-closes[i-1],up=Math.max(0,delta),down=Math.max(0,-delta);
   if(i<=period){gain+=up/period;loss+=down/period;if(i<period)return null;}else{gain=(gain*(period-1)+up)/period;loss=(loss*(period-1)+down)/period;}
   return gain===0&&loss===0?50:loss===0?100:100-100/(1+gain/loss);
  });
  return [line('rsi',`RSI${period}`,values,{guides:[30,70]})];
 }
 if(id==='atr'){
  let previous:number|null=null;
  const ranges=bars.map((b,i)=>Math.max(Number(b.high)-Number(b.low),i?Math.abs(Number(b.high)-closes[i-1]):0,i?Math.abs(Number(b.low)-closes[i-1]):0));
  return [line('atr',`ATR${period}`,ranges.map((value,i)=>{if(i+1<period)return null;previous=previous===null?average(ranges.slice(0,period)):(previous*(period-1)+value)/period;return previous;}))];
 }
 if(id==='vwap'){
  let day='',weighted=0,volume=0;
  return [line('vwap','UTC VWAP',bars.map(b=>{const next=b.open_time.slice(0,10);if(next!==day){day=next;weighted=0;volume=0;}weighted+=(Number(b.high)+Number(b.low)+Number(b.close))/3*Number(b.volume);volume+=Number(b.volume);return volume?weighted/volume:null;}))];
 }
 if(id==='obv'){
  let value=0;
  return [line('obv','OBV',bars.map((b,i)=>{if(i)value+=Math.sign(closes[i]-closes[i-1])*Number(b.volume);return value;}))];
 }
 if(id==='roc')return [line('roc',`ROC${period}`,closes.map((v,i)=>i<period?null:(v/closes[i-period]-1)*100),{guides:[0]})];
 if(id==='cci'){
  const typical=bars.map(b=>(Number(b.high)+Number(b.low)+Number(b.close))/3);
  return [line('cci',`CCI${period}`,typical.map((v,i)=>{if(i+1<period)return null;const window=typical.slice(i-period+1,i+1),mean=average(window),deviation=average(window.map(p=>Math.abs(p-mean)));return deviation===0?null:(v-mean)/(.015*deviation);}),{guides:[-100,100]})];
 }
 const k=bars.map((_,i)=>{if(i+1<period)return null;const window=bars.slice(i-period+1,i+1),high=Math.max(...window.map(b=>Number(b.high))),low=Math.min(...window.map(b=>Number(b.low)));return high===low?null:100*(closes[i]-low)/(high-low);});
 if(id==='wr')return [line('wr',`%R${period}`,k.map(v=>v===null?null:v-100),{guides:[-80,-20]})];
 return [line('stoch-k',`%K${period}`,k,{guides:[20,80]}),line('stoch-d',`%D${params.smooth}`,moving(k,params.smooth),{color:'#e9a22c'})];
}
export function indicatorLabel(config:IndicatorConfig){
 const definition=indicatorCatalog[config.id],params=Object.values(config.params);
 return `${definition.name}${params.length?` (${params.join(', ')})`:''}`;
}
export function analysisSnapshot(bars:Candle[],configs:IndicatorConfig[],rows:IndicatorRow[]=[]){
 const find=(id:IndicatorId)=>configs.find(c=>c.id===id)??{id,visible:false,params:indicatorCatalog[id].defaults};
 const value=(id:IndicatorId)=>calculateIndicator(bars,find(id))[0]?.values.at(-1)??null;
 const close=Number(bars.at(-1)?.close),fast=value('sma'),slow=value('smaSlow'),rsi=value('rsi'),atr=value('atr');
 const trend=fast===null||slow===null?'unavailable':close>=fast&&fast>=slow?'up':close<fast&&fast<slow?'down':'mixed';
 const momentum=rsi===null?'预热不足':rsi>=70?'高位':rsi<=30?'低位':rsi>=55?'偏强':rsi<=45?'偏弱':'中性';
 const window=bars.slice(-20),high=window.length?Math.max(...window.map(b=>Number(b.high))):null,low=window.length?Math.min(...window.map(b=>Number(b.low))):null;
 const last=bars.at(-1),events=rows.flatMap(row=>row.structure_breaks).slice(-8).reverse();
 return {close,fast,slow,rsi,atr,trend,momentum,high,low,events,last,fastPeriod:find('sma').params.period,slowPeriod:find('smaSlow').params.period,rsiPeriod:find('rsi').params.period,atrPeriod:find('atr').params.period};
}
