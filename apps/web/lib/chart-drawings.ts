import type {PriceBar, PriceContext} from './price-action';

export type DrawingPoint={time:number;price:number};
export type DrawingKind='horizontal'|'trend'|'box'|'channel'|'fibonacci';
export type ChartDrawing={id:string;kind:DrawingKind;points:DrawingPoint[];locked:boolean;label:string};
export const drawingCount:Record<DrawingKind,number>={horizontal:1,trend:2,box:2,channel:3,fibonacci:2};
export const drawingLabels:Record<DrawingKind,string>={horizontal:'水平线',trend:'趋势线',box:'价格箱体',channel:'平行通道',fibonacci:'斐波那契'};
export function drawingScope(context:PriceContext){return JSON.stringify([context.instrumentId,context.source,context.timeframe]);}
export function drawingStorageKey(context:PriceContext){return 'fc666.chart.drawings.v1:'+encodeURIComponent(drawingScope(context));}
export function validDrawing(value:unknown):value is ChartDrawing {
 if(!value||typeof value!=='object')return false;
 const d=value as ChartDrawing;
 return typeof d.id==='string'&&d.id.length>0&&d.id.length<=100&&Object.hasOwn(drawingCount,d.kind)&&typeof d.locked==='boolean'&&typeof d.label==='string'&&d.label.length<=80&&Array.isArray(d.points)&&d.points.length===drawingCount[d.kind]&&d.points.every(p=>p&&Number.isSafeInteger(p.time)&&p.time>=0&&p.time<=8640000000000000&&Number.isFinite(p.price)&&p.price>0)&&(!['trend','channel','fibonacci','box'].includes(d.kind)||d.points[0].time!==d.points[1].time)&&(!['box','fibonacci'].includes(d.kind)||d.points[0].price!==d.points[1].price);
}
export function decodeDrawings(text:string,context:PriceContext):ChartDrawing[]{
 const value:unknown=JSON.parse(text);
 if(!value||typeof value!=='object')throw new Error('绘图格式无效');
 const data=value as {version?:unknown;scope?:unknown;drawings?:unknown};
 if(data.version!==1||data.scope!==drawingScope(context)||!Array.isArray(data.drawings)||data.drawings.length>100||!data.drawings.every(validDrawing)||new Set(data.drawings.map(d=>d.id)).size!==data.drawings.length)throw new Error('绘图版本、产品或坐标校验失败');
 return data.drawings;
}
export function encodeDrawings(drawings:readonly ChartDrawing[],context:PriceContext){return JSON.stringify({version:1,scope:drawingScope(context),drawings});}
export function snapDrawingPoint(point:DrawingPoint,bars:readonly PriceBar[],snap:boolean):DrawingPoint {
 if(!snap)return point;
 const bar=bars.find(bar=>bar.time===point.time);if(!bar)return point;
 const price=[bar.open,bar.high,bar.low,bar.close].reduce((nearest,value)=>Math.abs(value-point.price)<Math.abs(nearest-point.price)?value:nearest,bar.close);
 return {time:bar.time,price};
}
