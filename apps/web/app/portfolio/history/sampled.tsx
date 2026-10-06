'use client';
import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
const decimal=z.string().regex(/^-?\d+(\.\d+)?([Ee][+-]?\d+)?$/);
const interval=z.object({peak_snapshot_id:z.string().uuid(),trough_snapshot_id:z.string().uuid()}).nullable();
const schema=z.object({version:z.literal('paper-portfolio-sampled-v1'),scenario_id:z.string().uuid(),trading_enabled:z.literal(false),
 window:z.object({limit:z.number().int().min(1).max(8),points:z.number().int(),has_earlier:z.boolean()}),
 points:z.array(z.object({snapshot_id:z.string().uuid(),price_as_of:z.string(),equity:decimal.nullable(),segment_id:z.string().nullable(),plot_index:z.number().int().nonnegative(),plot_height:decimal.refine(value=>Number(value)>=0&&Number(value)<=1).nullable()})),
 segments:z.array(z.object({segment_id:z.string().uuid(),sampled:z.object({observations:z.number().int(),measurement_status:z.enum(['INSUFFICIENT_OBSERVATIONS','OBSERVED_POINTS']),peak_equity:decimal,max_drawdown_amount:decimal.nullable(),max_drawdown_ratio:decimal.nullable(),ratio_unavailable_reason:z.string().nullable(),amount_interval:interval,ratio_interval:interval})})),
 plot:z.object({low_equity:decimal.nullable(),high_equity:decimal.nullable(),x_axis:z.literal('STORAGE_ORDER')}),inputs_sha256:z.string().regex(/^[0-9a-f]{64}$/)});
type Sampled=z.infer<typeof schema>;
export default function Sampled({scenarioId,disabled}:{scenarioId:string;disabled:boolean}){
 const [view,setView]=useState<Sampled|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState<string|null>(null);
 const raw=useRef<unknown>(null),request=useRef<AbortController|null>(null);
 useEffect(()=>()=>request.current?.abort(),[]);
 async function load(){request.current?.abort();const controller=new AbortController();request.current=controller;setBusy(true);setView(null);setSelected(null);raw.current=null;setError('');try{
 const response=await fetch(`/api/portfolio/scenarios/${scenarioId}/sampled?limit=8`,{cache:'no-store',signal:controller.signal});
 if(!response.ok)throw Error(`采样权益分析不可用 (${response.status})`);const result=await response.json();const parsed=schema.parse(result);
 if(parsed.scenario_id!==scenarioId)throw Error('返回场景不一致');raw.current=result;setView(parsed);
 }catch(e){if(!controller.signal.aborted)setError(String(e));}finally{if(!controller.signal.aborted)setBusy(false);}}
 const chosen=view?.points.find(point=>point.snapshot_id===selected);
 const x=(index:number)=>42+index/Math.max((view?.points.length??1)-1,1)*536;
 const y=(height:string)=>180-Number(height)*140;
 return <section data-testid="portfolio-sampled"><h2>分段采样权益与观察点回撤</h2><p>最近最多 8 个已保存观察。每个可比区间独立计算观察点回撤；未观察到的分钟内波动不包含在指标中。单点区间不提供回撤指标。</p><button disabled={busy||disabled} onClick={()=>void load()}>查看采样权益</button>{error?<p role="alert">{error}</p>:null}
 {view?<div data-testid="sampled-result"><p>{view.window.has_earlier?'窗口外还有更早记录；峰值从当前区间首点重新开始。':'使用本次读取的已保存记录；峰值从各可比区间首点开始。'}新增快照后请重新读取。</p>
 {view.points.length?<><svg role="img" aria-label="分段采样权益图" viewBox="0 0 620 250" style={{width:'100%',maxWidth:900,display:'block'}}><title>保存顺序中的采样权益；仅同一可比区间连接</title><line x1="42" x2="578" y1="180" y2="180" stroke="currentColor" opacity="0.3"/>
 {view.segments.map(segment=>{const members=view.points.filter(point=>point.segment_id===segment.segment_id&&point.plot_height!==null);return members.length>1?<polyline key={segment.segment_id} data-segment={segment.segment_id} points={members.map(point=>`${x(point.plot_index)},${y(point.plot_height!)}`).join(' ')} fill="none" stroke="#38bdf8" strokeWidth="2"/>:null;})}
 {view.points.map(point=><g key={point.snapshot_id}>{point.plot_height===null?<text x={x(point.plot_index)} y="219" textAnchor="middle" fill="#f97316" data-unavailable="true">×<title>不可汇总，未计作零权益</title></text>:<circle data-snapshot={point.snapshot_id} cx={x(point.plot_index)} cy={y(point.plot_height)} r="5" fill="#38bdf8" tabIndex={0} onFocus={()=>setSelected(point.snapshot_id)} onMouseEnter={()=>setSelected(point.snapshot_id)}><title>{point.price_as_of} · {point.equity} USDT</title></circle>}<text x={x(point.plot_index)} y="244" textAnchor="middle" fill="currentColor" fontSize="12">{point.plot_index+1}</text></g>)}</svg>
 <p>横轴为保存顺序，间距不代表时间长度；线段仅连接相邻观察，不代表中间时点权益。纵轴范围 {view.plot.low_equity??'未确认'} 至 {view.plot.high_equity??'未确认'} USDT。× 表示不可汇总，未计作零。悬停或聚焦观察点查看精确值。</p>{chosen?<p style={{overflowWrap:'anywhere'}}>选中观察：{chosen.snapshot_id}；报价 UTC {chosen.price_as_of}；权益 {chosen.equity??'不可汇总'} USDT。</p>:null}</>:<p>尚无已保存观察。</p>}
 <div className="research-table"><table><thead><tr><th>保存顺序</th><th>报价 UTC</th><th>精确权益 USDT</th></tr></thead><tbody>{view.points.map(point=><tr key={point.snapshot_id}><td>{point.plot_index+1}</td><td>{point.price_as_of}</td><td>{point.equity??'不可汇总'}</td></tr>)}</tbody></table></div>
 <h3>各区间的观察点指标</h3>{view.segments.map(segment=><div key={segment.segment_id} data-testid="sampled-segment" style={{overflowWrap:'anywhere'}}><p>区间 {segment.segment_id} · {segment.sampled.observations} 个观察 · 区间内最高观察权益 {segment.sampled.peak_equity} USDT</p><p>最大观察点回撤金额：{segment.sampled.max_drawdown_amount??'观察不足'}{segment.sampled.max_drawdown_amount!==null?' USDT':''}；最大观察点回撤比例：{segment.sampled.max_drawdown_ratio??(segment.sampled.ratio_unavailable_reason==='NO_POSITIVE_PEAK'?'没有正权益峰值，比例不可计算':'观察不足')}（0..1，分母为该观察之前的区间内正权益峰值）。</p>{segment.sampled.amount_interval?<p>金额对应峰值 → 低点：{segment.sampled.amount_interval.peak_snapshot_id} → {segment.sampled.amount_interval.trough_snapshot_id}</p>:null}{segment.sampled.ratio_interval?<p>比例对应峰值 → 低点：{segment.sampled.ratio_interval.peak_snapshot_id} → {segment.sampled.ratio_interval.trough_snapshot_id}</p>:null}</div>)}
 <button onClick={()=>{if(!raw.current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(raw.current)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`fc666-portfolio-sampled-${view.inputs_sha256}.json`;link.click();URL.revokeObjectURL(url);}}>下载采样权益分析</button><p>导出保留完整快照和精确指标，可离线复算。图形只辅助展示；无跨区间汇总回撤、连续回撤、收益率或年化指标。</p></div>:null}</section>;
}
