import type {PriceContext} from './price-action';

export const DERIVATIVE_SYMBOLS=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT'] as const;
export type DerivativeSymbol=typeof DERIVATIVE_SYMBOLS[number];
export const DERIVATIVES_MAX_AGE=120000;
export type DerivativesSnapshot={symbol:DerivativeSymbol;category:'USDT-FUTURES';source:'bitget.public:v3:USDT-FUTURES';baseAsset:string;openInterest:string;oiAsOf:number;fundingRate:string;fundingAsOf:number;fundingIntervalHours:number;nextUpdate:number;fetchedAt:number};
type RecordValue=Record<string,unknown>;
function object(value:unknown):RecordValue {if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('合约背景数据结构无效。');return value as RecordValue;}
export function derivativeSymbol(instrumentId:string):DerivativeSymbol|null {const match=/^bitget:spot:(BTC|ETH|SOL|XRP|BNB|DOGE|ADA|AVAX)-USDT$/.exec(instrumentId);return match?(match[1]+'USDT') as DerivativeSymbol:null;}
export function supportsDerivatives(context:PriceContext):boolean{return context.source==='bitget.public'&&derivativeSymbol(context.instrumentId)!==null;}
function numericString(value:unknown,nonnegative=false):string {if(typeof value!=='string'||!/^[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?$/.test(value)||!Number.isFinite(Number(value))||(nonnegative&&Number(value)<0))throw new Error('合约背景数值无效。');return value;}
function timestamp(value:unknown):number {const number=typeof value==='string'&&/^\d+$/.test(value)?Number(value):value;if(typeof number!=='number'||!Number.isSafeInteger(number)||number<=0||number>8640000000000000)throw new Error('合约背景时间无效。');return number;}
function fresh(value:number,now:number){if(!Number.isSafeInteger(now)||now-value>DERIVATIVES_MAX_AGE||value-now>5000)throw new Error('合约背景已过期或时间异常，请重新读取。');}
function envelope(value:unknown,now:number){const data=object(value);if(data.code!=='00000')throw new Error('Bitget 未返回成功状态。');const requestTime=timestamp(data.requestTime);fresh(requestTime,now);return {data:data.data,requestTime};}
export function parseDerivatives(oiValue:unknown,fundingValue:unknown,symbol:DerivativeSymbol,now:number):DerivativesSnapshot {
 if(!DERIVATIVE_SYMBOLS.includes(symbol))throw new Error('不支持的合约产品。');
 const oi=envelope(oiValue,now),funding=envelope(fundingValue,now),oiData=object(oi.data);
 if(!Array.isArray(oiData.list)||oiData.list.length!==1||!Array.isArray(funding.data)||funding.data.length!==1)throw new Error('合约背景必须返回唯一匹配产品。');
 const interest=object(oiData.list[0]),rate=object(funding.data[0]);
 if(interest.symbol!==symbol||rate.symbol!==symbol)throw new Error('合约背景产品不匹配。');
 const oiAsOf=timestamp(oiData.ts);fresh(oiAsOf,now);
 if(typeof rate.fundingRateInterval!=='string'||!/^\d+$/.test(rate.fundingRateInterval))throw new Error('资金费率周期无效。');
 const hours=Number(rate.fundingRateInterval),nextUpdate=timestamp(rate.nextUpdate);
 if(!Number.isSafeInteger(hours)||hours<=0||hours>24*365||nextUpdate<=funding.requestTime||nextUpdate-funding.requestTime>hours*3600000+60000)throw new Error('资金费率周期或下次结算时间无效。');
 const fundingRate=numericString(rate.fundingRate);if(!Number.isFinite(Number(fundingRate)*100))throw new Error('资金费率超出有效显示范围。');
 return {symbol,category:'USDT-FUTURES',source:'bitget.public:v3:USDT-FUTURES',baseAsset:symbol.slice(0,-4),openInterest:numericString(interest.openInterest,true),oiAsOf,fundingRate,fundingAsOf:funding.requestTime,fundingIntervalHours:hours,nextUpdate,fetchedAt:now};
}
/** Validate even our same-origin transport; never substitute stale values or fabricated zeroes. */
export function validateDerivativesSnapshot(value:unknown,symbol:DerivativeSymbol,now:number):DerivativesSnapshot {
 const row=object(value);
 if(row.symbol!==symbol||row.category!=='USDT-FUTURES'||row.source!=='bitget.public:v3:USDT-FUTURES'||row.baseAsset!==symbol.slice(0,-4))throw new Error('合约背景来源或产品不匹配。');
 const parsed=parseDerivatives({code:'00000',requestTime:row.fetchedAt,data:{list:[{symbol,openInterest:row.openInterest}],ts:row.oiAsOf}},{code:'00000',requestTime:row.fundingAsOf,data:[{symbol,fundingRate:row.fundingRate,fundingRateInterval:String(row.fundingIntervalHours),nextUpdate:row.nextUpdate}]},symbol,now);
 return {...parsed,fetchedAt:timestamp(row.fetchedAt)};
}
export function derivativesStale(snapshot:DerivativesSnapshot,now:number):boolean {return [snapshot.fetchedAt,snapshot.oiAsOf,snapshot.fundingAsOf].some(value=>now-value>DERIVATIVES_MAX_AGE||value-now>5000)||snapshot.nextUpdate<=now;}

/** Only fixed public v3 paths, no credentials, redirects, caller URL, or trading endpoints. */
export async function fetchDerivativesSnapshot(symbol:DerivativeSymbol,fetcher:typeof fetch=fetch,now:()=>number=Date.now,timeoutMs=6000):Promise<DerivativesSnapshot>{
 if(!DERIVATIVE_SYMBOLS.includes(symbol))throw new Error('不支持的合约产品。');
 const signal=AbortSignal.timeout(timeoutMs);
 const values=await Promise.all(['open-interest','current-fund-rate'].map(async resource=>{
  const url=new URL('https://api.bitget.com/api/v3/market/'+resource);url.searchParams.set('category','USDT-FUTURES');url.searchParams.set('symbol',symbol);
  const response=await fetcher(url,{method:'GET',cache:'no-store',redirect:'error',credentials:'omit',headers:{Accept:'application/json'},signal});
  if(!response.ok)throw new Error(`Bitget 合约背景暂不可用（HTTP ${response.status}）。`);
  const text=await response.text();if(text.length>64000)throw new Error('合约背景响应过大。');
  try{return JSON.parse(text) as unknown;}catch{throw new Error('合约背景不是有效 JSON。');}
 }));
 return parseDerivatives(values[0],values[1],symbol,now());
}
