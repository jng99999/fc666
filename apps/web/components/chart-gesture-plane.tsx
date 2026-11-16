'use client';
import {useEffect,useRef,type PointerEvent} from 'react';
import {chartLocalPoint,type ChartPoint} from '../lib/chart-landscape';
import type {ChartCoordinates} from '../lib/chart-coordinates';
import './chart-gesture-plane.css';

/** Only visible in CSS-rotated mode; native library gestures remain active in natural landscape. */
export default function ChartGesturePlane({coordinates}:{coordinates:ChartCoordinates}){
 const pointers=useRef(new Map<number,ChartPoint>()),moved=useRef(false);
 const plane=useRef<HTMLDivElement>(null);
 useEffect(()=>{const element=plane.current;if(!element)return;const wheel=(event:WheelEvent)=>{event.preventDefault();coordinates.zoomAt?.(Math.exp(-event.deltaY*.002),chartLocalPoint(element,event.clientX,event.clientY).x);};element.addEventListener('wheel',wheel,{passive:false});return()=>element.removeEventListener('wheel',wheel);},[coordinates]);
 function down(event:PointerEvent<HTMLDivElement>){event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId);if(!pointers.current.size)moved.current=false;pointers.current.set(event.pointerId,chartLocalPoint(event.currentTarget,event.clientX,event.clientY));}
 function move(event:PointerEvent<HTMLDivElement>){
  const before=[...pointers.current.values()];if(!pointers.current.has(event.pointerId))return;
  event.preventDefault();const next=chartLocalPoint(event.currentTarget,event.clientX,event.clientY),previous=pointers.current.get(event.pointerId)!;
  if(Math.hypot(next.x-previous.x,next.y-previous.y)>1)moved.current=true;
  pointers.current.set(event.pointerId,next);const after=[...pointers.current.values()];
  if(before.length===1){coordinates.panByPixels?.(next.x-previous.x);return;}
  if(before.length===2){const oldDistance=Math.hypot(before[0].x-before[1].x,before[0].y-before[1].y),distance=Math.hypot(after[0].x-after[1].x,after[0].y-after[1].y);if(oldDistance>4&&distance>4)coordinates.zoomAt?.(distance/oldDistance,(after[0].x+after[1].x)/2);}
 }
 function up(event:PointerEvent<HTMLDivElement>){const point=pointers.current.get(event.pointerId);if(point&&!moved.current&&pointers.current.size===1)coordinates.inspectAt?.(point.x,point.y);pointers.current.delete(event.pointerId);}
 return <div ref={plane} className="chart-gesture-plane" data-testid="landscape-chart-gestures" style={{width:coordinates.size().width,height:coordinates.size().height}} aria-label="横屏图表：拖动平移，双指缩放，轻点查看价格" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={event=>pointers.current.delete(event.pointerId)} onLostPointerCapture={event=>pointers.current.delete(event.pointerId)}/>
}
