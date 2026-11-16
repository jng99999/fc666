export type ChartPoint={x:number;y:number};
export const mobileExchangeQuery='(max-width:760px), (max-width:1024px) and (hover:none) and (pointer:coarse)';
export function mobileChartViewport():boolean{return window.matchMedia(mobileExchangeQuery).matches;}
export function chartCandleColors(mobile:boolean){const up=mobile?'#00b8c4':'#08bc76',down=mobile?'#ef4778':'#f14c68';return {upColor:up,downColor:down,wickUpColor:up,wickDownColor:down};}
type Bounds={left:number;top:number;width:number;height:number};
/** A clockwise quarter turn swaps physical axes; all chart APIs still use local pixels. */
export function chartPointInBounds(point:ChartPoint,bounds:Bounds,size:{width:number;height:number},rotated:boolean):ChartPoint {
 if(bounds.width<=0||bounds.height<=0)return {x:NaN,y:NaN};
 return rotated?{x:(point.y-bounds.top)*size.width/bounds.height,y:(bounds.left+bounds.width-point.x)*size.height/bounds.width}:{x:(point.x-bounds.left)*size.width/bounds.width,y:(point.y-bounds.top)*size.height/bounds.height};
}
export function rotatedChartFrame(element:Element):boolean {
 const frame=element.closest('[data-chart-frame]');
 return !!frame&&getComputedStyle(frame).getPropertyValue('--chart-rotation').trim()==='1';
}
export function chartLocalPoint(element:Element,clientX:number,clientY:number):ChartPoint {
 return chartPointInBounds({x:clientX,y:clientY},element.getBoundingClientRect(),{width:element.clientWidth,height:element.clientHeight},rotatedChartFrame(element));
}
export function fitImmersiveChart(element:HTMLElement):void {
 const frame=element.closest<HTMLElement>('[data-chart-frame]');if(!frame)return;
 const box=frame.getBoundingClientRect(),host=element.getBoundingClientRect();
 const top=rotatedChartFrame(element)?box.right-host.right:host.top-box.top;
 element.style.height=`${Math.max(120,Math.min(frame.clientHeight-60,frame.clientHeight-top-10))}px`;
}
