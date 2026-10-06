'use client';
import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
const decimal=z.string().regex(/^-?\d+(\.\d+)?([Ee][+-]?\d+)?$/);
const schema=z.object({version:z.literal('paper-portfolio-continuity-v1'),scenario_id:z.string().uuid(),trading_enabled:z.literal(false),
 window:z.object({limit:z.number().int().min(1).max(8),has_earlier:z.boolean(),points:z.number().int(),order:z.literal('STORAGE_TIME_ASC')}),
 summary:z.object({complete:z.number().int(),unavailable:z.number().int(),comparable_links:z.number().int(),breaks:z.number().int()}),
 points:z.array(z.object({snapshot_id:z.string().uuid(),as_of:z.string(),price_as_of:z.string(),status:z.enum(['COMPLETE','UNAVAILABLE']),equity:decimal.nullable(),segment_id:z.string().nullable()})),
 edges:z.array(z.object({from_snapshot_id:z.string().uuid(),to_snapshot_id:z.string().uuid(),comparable:z.boolean(),reasons:z.array(z.string()),price_elapsed_seconds:z.number().int(),unobserved_minutes:z.number().int().nonnegative(),equity_change:decimal.nullable()})),
 segments:z.array(z.object({segment_id:z.string(),observations:z.number().int(),first_equity:decimal,last_equity:decimal,equity_change:decimal})),inputs_sha256:z.string().regex(/^[0-9a-f]{64}$/)});
type Analysis=z.infer<typeof schema>;
const labels:Record<string,string>={UNAVAILABLE_VALUATION:'至少一端不可汇总',READ_CLOCK_NOT_ADVANCING:'账户读取时钟未前进',PRICE_CLOCK_NOT_ADVANCING:'报价分钟相同或倒退',OBSERVATION_GAP:'中间缺少估值观察',WORKER_HEALTH_WARNING:'模拟执行进程健康未确认',ACCOUNT_DEFINITION_CHANGED:'账户冻结定义发生变化',INSTRUMENT_RULES_CHANGED:'市场规则发生变化',ACCOUNT_REVISION_REGRESSED:'账户版本倒退',OBSERVATION_PREFIX_CHANGED:'已接受观察前缀变化',ACCEPTED_DATA_PREFIX_CHANGED:'已接受行情前缀变化',LEDGER_PREFIX_CHANGED:'旧订单、成交或权益账本发生变化'};
export default function Continuity({scenarioId,disabled}:{scenarioId:string;disabled:boolean}){
 const [value,setValue]=useState<Analysis|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const raw=useRef<unknown>(null),request=useRef<AbortController|null>(null);
 useEffect(()=>()=>request.current?.abort(),[]);
 async function load(){request.current?.abort();const controller=new AbortController();request.current=controller;setBusy(true);setValue(null);raw.current=null;setError('');try{
 const response=await fetch(`/api/portfolio/scenarios/${scenarioId}/analysis?limit=8`,{cache:'no-store',signal:controller.signal});
 if(!response.ok)throw Error(`历史分析不可用 (${response.status})`);const result=await response.json();const parsed=schema.parse(result);
 if(parsed.scenario_id!==scenarioId)throw Error('返回场景不一致');raw.current=result;setValue(parsed);
 }catch(e){if(!controller.signal.aborted)setError(String(e));}finally{if(!controller.signal.aborted)setBusy(false);}}
 return <section data-testid="portfolio-continuity"><h2>历史快照可比性</h2><p>分析最近最多 8 个已保存快照，按保存时点排序。仅相邻有效分钟且账户输入可比时计算权益差；不补齐缺口，不计算连续收益率或回撤。</p><button disabled={busy||disabled} onClick={()=>void load()}>分析已保存历史</button>{error?<p role="alert">{error}</p>:null}
 {value?<div data-testid="continuity-result"><p>窗口：{value.window.points} 个快照；完整 {value.summary.complete} 个，不可汇总 {value.summary.unavailable} 个；可比连接 {value.summary.comparable_links} 条，中断 {value.summary.breaks} 条。{value.window.has_earlier?'还有更早记录，本次窗口未包含。':'这是本次读取中全部已保存记录。'}</p><p>此分析冻结于本次读取；新增快照后请重新分析。同一分钟的多次保存保留原记录，并切断比较。</p>
 {!value.points.length?<p>尚无已保存快照。</p>:null}
 <div className="research-table"><table><thead><tr><th>账户读取 UTC</th><th>报价分钟 UTC</th><th>状态</th><th>权益 USDT</th></tr></thead><tbody>{value.points.map(point=><tr key={point.snapshot_id}><td>{point.as_of}</td><td>{point.price_as_of}</td><td>{point.status}</td><td>{point.equity??'不可汇总'}</td></tr>)}</tbody></table></div>
 <h3>相邻观察</h3>{value.edges.map(edge=><p key={edge.to_snapshot_id} style={{overflowWrap:'anywhere'}}>快照 {edge.to_snapshot_id}：{edge.comparable?`可比较，权益变化 ${edge.equity_change} USDT`:`区间中断：${edge.reasons.map(reason=>labels[reason]??reason).join('；')}`}；报价间隔 {edge.price_elapsed_seconds} 秒，中间无估值观察 {edge.unobserved_minutes} 分钟。</p>)}
 <h3>可比区间</h3><p>单点区间的权益变化为零，仅表示尚无第二个可比观察。窗口外区间不参与本次分析。</p>{value.segments.map(segment=><p key={segment.segment_id} style={{overflowWrap:'anywhere'}}>区间 {segment.segment_id}：{segment.observations} 个观察，权益 {segment.first_equity} → {segment.last_equity} USDT；变化 {segment.equity_change} USDT。</p>)}
 <button onClick={()=>{if(!raw.current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(raw.current)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`fc666-portfolio-continuity-${value.inputs_sha256}.json`;link.click();URL.revokeObjectURL(url);}}>下载可比性分析</button><p>下载包含窗口内完整原快照，可离线复算；不证明窗口外数据完整或来源真实性。</p></div>:null}</section>;
}
