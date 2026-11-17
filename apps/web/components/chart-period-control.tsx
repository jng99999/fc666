'use client';
import {useEffect,useId,useRef,useState} from 'react';
import {chartPeriods,chartPeriodMs} from '../lib/chart-periods';

export default function ChartPeriodControl({value,onChange,disabled=false,mobile=false}:{value:string;onChange:(value:string)=>void;disabled?:boolean;mobile?:boolean}){
 const id=useId(),[amount,setAmount]=useState('2'),[unit,setUnit]=useState<'m'|'h'>('m'),[error,setError]=useState('');
 const presets=useRef<HTMLDivElement>(null);
 useEffect(()=>{const list=presets.current,selected=list?.querySelector<HTMLElement>('[aria-pressed=true]');if(list&&selected)list.scrollLeft+=selected.getBoundingClientRect().left-list.getBoundingClientRect().left-(list.clientWidth-selected.offsetWidth)/2;},[value]);
 return <div className="chart-period-control">
  <div ref={presets} className="chart-period-presets" role="group" aria-label="图表周期">{[...new Set([...chartPeriods,value])].map(period=><button type="button" key={period} disabled={disabled} aria-label={period} aria-pressed={value===period} onClick={()=>onChange(period)}>{period.endsWith('m')?period:period.toUpperCase()}</button>)}</div>
  <details className="chart-custom-period"><summary onClick={event=>{if(!mobile||event.currentTarget.closest('details')?.open)return;const selected=/^(\d+)(m|h|d)$/.exec(value);if(selected&&chartPeriodMs(value)){setAmount(selected[2]==='d'?'24':selected[1]);setUnit(selected[2]==='m'?'m':'h');}setError('');}}>自定义周期 <span>{mobile&&value.endsWith('m')?value:value.toUpperCase()}</span></summary><form noValidate onSubmit={event=>{event.preventDefault();if(disabled)return;const period=`${Number(amount)}${unit}`;if(!Number.isInteger(Number(amount))||!chartPeriodMs(period)){setError(unit==='m'?'请输入 1–60 的整数分钟数。':'请输入 1–24 的整数小时数。');return;}setError('');onChange(period);event.currentTarget.closest('details')?.removeAttribute('open');}}><label htmlFor={id}>周期数值<input id={id} type="number" inputMode="numeric" min="1" max={unit==='m'?60:24} step="1" value={amount} disabled={disabled} onChange={event=>{setAmount(event.target.value);setError('');}}/></label><div role="group" aria-label="周期单位"><button type="button" disabled={disabled} aria-pressed={unit==='m'} onClick={()=>{setUnit('m');setError('');}}>分钟</button><button type="button" disabled={disabled} aria-pressed={unit==='h'} onClick={()=>{setUnit('h');setError('');}}>小时</button></div><button type="submit" disabled={disabled}>应用周期</button>{error?<p role="alert">{error}</p>:null}<small>支持 1–60 分钟、1–24 小时；按 UTC 边界合成完整 K 线。</small></form></details>
 </div>;
}
