import type {PriceAnnotation} from './price-action';

/** Extend fixed anchors only through this object's observed lifetime, never into future bars. */
export function chartTrendGeometry(annotation:PriceAnnotation,windowStart:number,lastOpen:number):PriceAnnotation {
 if(!['trend','channel'].includes(annotation.kind)||!annotation.points||annotation.points.length<2)return annotation;
 const start=Math.max(annotation.start,windowStart),end=Math.min(annotation.end,lastOpen),[a,b,c,d]=annotation.points;
 if(end<=start||b.time<=a.time||annotation.kind==='channel'&&(!c||!d||d.time<=c.time))return annotation;
 const line=(first:typeof a,second:typeof b)=>[start,end].map(time=>({time,price:first.price+(second.price-first.price)*(time-first.time)/(second.time-first.time)}));
 const points=[...line(a,b),...(annotation.kind==='channel'?line(c,d):[])];
 if(points.some(point=>!Number.isFinite(point.price)||point.price<=0))return annotation;
 return {...annotation,start,end,points,low:Math.min(...points.map(p=>p.price)),high:Math.max(...points.map(p=>p.price))};
}
