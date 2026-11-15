import type {IChartApi, ISeriesApi, SeriesType, UTCTimestamp} from 'lightweight-charts';

/** UTC milliseconds and price units throughout; only this bridge converts chart seconds. */
export type ChartCoordinates = {
 timeToX:(time:number)=>number|null;
 priceToY:(price:number)=>number|null;
 xToTime:(x:number)=>number|null;
 yToPrice:(y:number)=>number|null;
 size:()=>{width:number;height:number};
 subscribe:(listener:()=>void)=>()=>void;
 dispose:()=>void;
};

export function chartCoordinates(chart:IChartApi,series:ISeriesApi<SeriesType>,element:HTMLElement,times:()=>readonly number[]):ChartCoordinates {
 const scale=chart.timeScale();
 const subscriptions=new Set<()=>void>();let disposed=false;
 return {
  timeToX(time){
   const exact=scale.timeToCoordinate((time/1000) as UTCTimestamp);
   if(exact!==null)return exact;
   const rows=times();if(!rows.length)return null;
   // Endpoints need not coincide with a candle open (e.g. confirmation at close).
   // Interpolate through actual candle indices, never through a screen-sized approximation.
   let lo=0,hi=rows.length-1;
   while(lo<hi){const mid=Math.floor((lo+hi)/2);if(rows[mid]<time)lo=mid+1;else hi=mid;}
   const right=lo,left=Math.max(0,right-1);
   if(time<rows[0]||time>rows.at(-1)!)return null;
   const logical=left===right?left:left+(time-rows[left])/(rows[right]-rows[left]);
   return scale.logicalToCoordinate(logical as Parameters<typeof scale.logicalToCoordinate>[0]);
  },
  priceToY:price=>series.priceToCoordinate(price),
  xToTime(x){const logical=scale.coordinateToLogical(x),rows=times();if(logical===null||!rows.length)return null;return rows[Math.max(0,Math.min(rows.length-1,Math.round(Number(logical))))];},
  yToPrice:y=>series.coordinateToPrice(y),
  size:()=>({width:scale.width(),height:chart.panes()[0]?.getHeight()??0}),
  subscribe(listener){
   if(disposed)return()=>{};
   let frame=0,active=true;
   const refresh=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{if(active)listener();});};
   scale.subscribeVisibleLogicalRangeChange(refresh);chart.subscribeCrosshairMove(refresh);
   element.addEventListener('pointermove',refresh,{passive:true});element.addEventListener('wheel',refresh,{passive:true});
   const observer=new ResizeObserver(refresh);observer.observe(element);refresh();
   const unsubscribe=()=>{if(!active)return;active=false;cancelAnimationFrame(frame);observer.disconnect();scale.unsubscribeVisibleLogicalRangeChange(refresh);chart.unsubscribeCrosshairMove(refresh);element.removeEventListener('pointermove',refresh);element.removeEventListener('wheel',refresh);subscriptions.delete(unsubscribe);};
   subscriptions.add(unsubscribe);return unsubscribe;
  },
  dispose(){for(const unsubscribe of subscriptions)unsubscribe();disposed=true;},
 };
}
