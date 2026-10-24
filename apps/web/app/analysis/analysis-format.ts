// Formatting only; these functions never change source bars or order precision.
export function decimalPlaces(raw:string|undefined){
 const match=raw?.match(/^\d+(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/);
 if(!match)return null;
 const digits=Math.max(0,(match[1]??'').replace(/0+$/,'').length-Number(match[2]??0));
 return Number.isInteger(digits)&&digits<=18?digits:null;
}
export function displayPrecision(symbol:string,tick:string|undefined,raw:string|undefined){
 if(symbol==='BTCUSDT'||symbol==='ETHUSDT')return 2;
 const validTick=tick!==undefined&&Number.isFinite(Number(tick))&&Number(tick)>0?decimalPlaces(tick):null;
 return validTick??decimalPlaces(raw);
}
export function formatPrice(value:number|null|undefined,symbol:string,tick:string|undefined,raw:string|undefined){
 if(value==null||!Number.isFinite(value))return '—';
 const digits=displayPrecision(symbol,tick,raw);
 return value.toLocaleString('zh-CN',digits===null?{maximumFractionDigits:18}:{minimumFractionDigits:digits,maximumFractionDigits:digits});
}
