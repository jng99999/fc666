'use client';
import Link from 'next/link';
import Continuity from './continuity';
import Sampled from './sampled';
import RiskHistory from './risk';
import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
type Scenario={scenario_id:string;definition:{name:string;session_ids:string[];limits:{max_gross_weight:string;max_asset_weight:string}}};
type Summary={snapshot_id:string;as_of:string;status:string;totals:{equity:string;net_pnl:string}|null};
type Pending={path:string;body:unknown};
const KEY='fc666.portfolio.history.pending.v1';
const Selected=z.array(z.uuid()).min(1).max(8).refine(ids=>new Set(ids).size===ids.length);
const validLimit=(value:string)=>/^-?\d+(\.\d+)?([Ee][+-]?\d+)?$/.test(value)&&Number.isFinite(Number(value))&&Number(value)>=0&&Number(value)<=1;
async function get(path:string){const response=await fetch(`/api/portfolio/${path}`,{cache:'no-store'});if(!response.ok)throw Error(`历史读取失败 (${response.status})`);return response.json();}
export default function History(){
 const [scenarios,setScenarios]=useState<Scenario[]>([]),[scene,setScene]=useState<Scenario|null>(null),[rows,setRows]=useState<Summary[]>([]),[cursor,setCursor]=useState<string|null>(null),[sceneCursor,setSceneCursor]=useState<string|null>(null);
 const [name,setName]=useState(''),[gross,setGross]=useState('0.8'),[asset,setAsset]=useState('0.6'),[pending,setPending]=useState<Pending|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[detail,setDetail]=useState<Record<string,unknown>|null>(null);
 const [selectedIds,setSelectedIds]=useState<string[]>([]);const locked=useRef(false);
 const validName=name.trim().length>0&&name.length<=80&&!/[\x00-\x1f\x7f]/.test(name);
 const validLimits=validLimit(gross)&&validLimit(asset);
 async function listScenes(next?:string){const data=await get(`scenarios?limit=10${next?`&cursor=${encodeURIComponent(next)}`:''}`);setScenarios(previous=>next?[...previous,...data.items]:data.items);setSceneCursor(data.next_cursor);}
 async function listRows(current:Scenario,next?:string){const data=await get(`scenarios/${current.scenario_id}/snapshots?limit=10${next?`&cursor=${encodeURIComponent(next)}`:''}`);setRows(previous=>next?[...previous,...data.items]:data.items);setCursor(data.next_cursor);}
 useEffect(()=>{try{const stored=localStorage.getItem(KEY);if(stored)setPending(JSON.parse(stored));const ids=Selected.safeParse(JSON.parse(localStorage.getItem('fc666.portfolio.selection.v1')??'[]'));if(ids.success)setSelectedIds(ids.data);}catch{setError('未能恢复账户选择或待确认请求');}void listScenes().catch(e=>setError(String(e)));},[]);
 async function submit(action:Pending){if(locked.current)return;locked.current=true;setBusy(true);setError('');setPending(action);try{localStorage.setItem(KEY,JSON.stringify(action));
 const response=await fetch(`/api/portfolio/${action.path}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(action.body)});
 if([404,422,429].includes(response.status)){localStorage.removeItem(KEY);setPending(null);throw Error(`请求已拒绝 (${response.status})，请检查账户、名称、阈值或容量。`);}
 if(!response.ok)throw Error(`提交未确认 (${response.status})，可使用相同请求重试；不要重复新建。`);
 const data=await response.json();localStorage.removeItem(KEY);setPending(null);
 if(action.path==='scenarios'){setScene(data);setRows([]);setCursor(null);setDetail(null);await listScenes();}
 else{setDetail(data);const current=await get(`scenarios/${data.scenario_id}`);setScene(current);await listRows(current);}
 }catch(e){setError(String(e));}finally{locked.current=false;setBusy(false);}}
 async function select(current:Scenario){setBusy(true);setError('');setScene(current);setRows([]);setDetail(null);setCursor(null);try{await listRows(current);}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <main className="research-page"><h1>模拟组合场景与估值历史</h1><p>场景冻结账户和风险提示阈值；历史快照不会随行情更新。未启用实盘。风险阈值仅提示，不阻止订单。</p>
 <section><h2>保存新场景</h2><p>已选择 {selectedIds.length} 个实时模拟账户。<Link href="/portfolio">返回组合估值调整选择 →</Link> 此处阈值为新场景独立设定。</p><div className="research-fields"><label>场景名称<input aria-label="场景名称" maxLength={80} value={name} disabled={busy||!!pending} onChange={e=>setName(e.target.value)}/></label><label>总敞口阈值（0–1）<input aria-label="历史总敞口阈值" aria-invalid={!validLimit(gross)} inputMode="decimal" value={gross} disabled={busy||!!pending} onChange={e=>setGross(e.target.value)}/></label><label>单币种阈值（0–1）<input aria-label="历史单币种阈值" aria-invalid={!validLimit(asset)} inputMode="decimal" value={asset} disabled={busy||!!pending} onChange={e=>setAsset(e.target.value)}/></label></div>
 {!validLimits?<p role="alert">风险提示阈值必须是 0 到 1 之间的数字。</p>:null}
 <div className="research-submit"><button disabled={busy||!!pending||!validName||!validLimits||!selectedIds.length} onClick={()=>{try{const ids=Selected.parse(JSON.parse(localStorage.getItem('fc666.portfolio.selection.v1')??'[]'));void submit({path:'scenarios',body:{request_id:crypto.randomUUID(),definition:{name,session_ids:ids,limits:{max_gross_weight:gross,max_asset_weight:asset}}}});}catch{setError('账户选择无效，请返回组合估值重新选择');}}}>冻结并保存场景</button><small>保存后账户与阈值固定，估值快照需单独保存。</small></div></section>
 {pending?<section><p>有一个提交结果待确认。重试会复用原请求编号和完整内容，成功提交不会重复创建。</p><button disabled={busy} onClick={()=>void submit(pending)}>重试原请求</button></section>:null}{error?<p role="alert">{error}</p>:null}
 <section><h2>已保存场景</h2>{scenarios.map(current=><p key={current.scenario_id}><button disabled={busy} onClick={()=>void select(current)}>{current.definition.name}</button></p>)}{sceneCursor?<button disabled={busy} onClick={()=>void listScenes(sceneCursor).catch(e=>setError(String(e)))}>更多场景</button>:null}</section>
 {scene?<section><h2>{scene.definition.name}</h2><p style={{overflowWrap:'anywhere'}}>冻结账户：{scene.definition.session_ids.join(' / ')}<br/>总敞口阈值：{scene.definition.limits.max_gross_weight}；单币种阈值：{scene.definition.limits.max_asset_weight}</p><button disabled={busy||!!pending} onClick={()=>void submit({path:`scenarios/${scene.scenario_id}/snapshots`,body:{request_id:crypto.randomUUID()}})}>保存当前估值快照</button><p>报价或账本未确认时仍保存“不可汇总”结果，权益不计作零。历史点可用于区间内观察点回撤；不计算收益率或连续回撤。</p><div className="research-table"><table><thead><tr><th>读取时点 UTC</th><th>状态</th><th>权益 USDT</th><th>净盈亏 USDT</th><th>快照</th></tr></thead><tbody>{rows.map(row=><tr key={row.snapshot_id}><td>{row.as_of}</td><td>{row.status}</td><td>{row.totals?.equity??'不可汇总'}</td><td>{row.totals?.net_pnl??'不可汇总'}</td><td><button disabled={busy} onClick={()=>{setBusy(true);setDetail(null);void get(`scenarios/${scene.scenario_id}/snapshots/${row.snapshot_id}`).then(setDetail).catch(e=>setError(String(e))).finally(()=>setBusy(false));}}>查看快照</button></td></tr>)}</tbody></table></div>{cursor?<button disabled={busy} onClick={()=>void listRows(scene,cursor).catch(e=>setError(String(e)))}>更多快照</button>:null}</section>:null}
 {scene?<Continuity key={`continuity:${scene.scenario_id}`} scenarioId={scene.scenario_id} disabled={busy}/>:null}
 {scene?<Sampled key={`sampled:${scene.scenario_id}`} scenarioId={scene.scenario_id} disabled={busy}/>:null}
 {scene?<RiskHistory key={`risk:${scene.scenario_id}`} scenarioId={scene.scenario_id} disabled={busy}/>:null}
 {detail?<section data-testid="history-detail"><h2>已保存的完整快照</h2><p style={{overflowWrap:'anywhere'}}>快照编号：{String(detail.snapshot_id)}<br/>保存时点 UTC：{String(detail.created_at)}<br/>报告 SHA-256：{String(detail.report_sha256)}</p><button onClick={()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(detail,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`fc666-portfolio-history-${detail.snapshot_id}.json`;link.click();URL.revokeObjectURL(url);}}>下载历史快照</button><p>完整输入可离线复算。哈希用于内部一致性核对，不是来源签名或身份认证。</p></section>:null}</main>;
}
