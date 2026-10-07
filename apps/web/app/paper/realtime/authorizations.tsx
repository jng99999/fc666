'use client';
import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
const payloadSchema=z.object({
 decision:z.enum(['ALLOW_SIMULATION','DENY_SIMULATION']),reason:z.string().nullable(),
 projection_only:z.literal(true),external_submission_allowed:z.literal(false),
 proposed_order:z.object({side:z.enum(['BUY','SELL']),status:z.string(),execution_at:z.string()}),
 projected_fill:z.object({quantity:z.string(),price:z.string(),fee:z.string()}).nullable(),
});
const batchSchema=z.object({
 preparation_id:z.string(),status:z.enum(['PREPARED','CONSUMED','CANCELLED']),
 coverage:z.enum(['VERIFIED','LEGACY_UNAVAILABLE']),
 authorizations:z.array(z.object({authorization_id:z.string(),recorded_at:z.string(),payload:payloadSchema})),
});
const schema=z.object({
 version:z.literal('paper-simulated-authorization-v1'),session_id:z.uuid(),
 trading_enabled:z.literal(false),external_submission_supported:z.literal(false),automatic_replay:z.literal(false),
 total_batches:z.number().int().nonnegative(),window_limit:z.literal(20),has_older:z.boolean(),
 account_gate:z.object({account_status:z.string(),buy_allowed:z.boolean(),sell_allowed:z.boolean(),entry_halted:z.boolean(),external_submission_allowed:z.literal(false)}),
 batches:z.array(batchSchema),
});
type Report=z.infer<typeof schema>;
export default function Authorizations({id,disabled}:{id:string;disabled:boolean}){
 const [value,setValue]=useState<Report|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');const request=useRef<AbortController|null>(null);
 useEffect(()=>()=>request.current?.abort(),[]);
 async function inspect(){request.current?.abort();const controller=new AbortController();request.current=controller;setBusy(true);setValue(null);setError('');try{const response=await fetch(`/api/paper/streams/${id}/authorizations`,{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error(`逐单授权核对未确认 (${response.status})`);const parsed=schema.parse(await response.json());if(parsed.session_id!==id)throw Error('账户不一致');setValue(parsed);}catch(e){if(!controller.signal.aborted)setError(String(e));}finally{if(!controller.signal.aborted)setBusy(false);}}
 const rows=value?.batches.flatMap(batch=>batch.authorizations.map(row=>({...row,batchStatus:batch.status})))??[];
 return <section data-testid="paper-authorizations"><h2>模拟订单事前规则授权</h2><p>准备时保存每笔订单的预计数量、价格、费用和允许或拒绝原因，消费前逐项重算核对。预计结果不是已成交回执，也不是用户身份授权或交易所提交许可；此处只读。</p><button disabled={busy||disabled} onClick={()=>void inspect()}>核对逐单模拟授权</button>{error?<p role="alert">{error}</p>:null}{value?<div data-testid="authorization-result"><p>当前账户 {value.account_gate.account_status} · 新买入规则 {value.account_gate.buy_allowed?'允许，仍须通过逐单风控与行情复核':'禁止'} · 策略卖出规则 {value.account_gate.sell_allowed?'允许，仍须满足库存与策略条件':'禁止'}</p><p>共保存 {value.total_batches} 批次；仅核对最新20批次{value.has_older?'，更早记录不在本次结果中':''}。已核对批次 {value.batches.filter(b=>b.coverage==='VERIFIED').length} · 旧批次无事前授权 {value.batches.filter(b=>b.coverage==='LEGACY_UNAVAILABLE').length} · 逐单规则记录 {rows.length}。空列表不表示旧账户没有历史成交。</p>{rows.length?<div className="research-table"><table><thead><tr><th>授权编号</th><th>批次状态 / 方向</th><th>事前规则判定</th><th>预计数量 / 价格 / 费用</th><th>授权保存 UTC</th></tr></thead><tbody>{rows.slice(-100).map(row=><tr key={row.authorization_id}><td style={{overflowWrap:'anywhere'}}>{row.authorization_id}</td><td>{row.batchStatus} / {row.payload.proposed_order.side}</td><td>{row.payload.decision==='ALLOW_SIMULATION'?'允许本次模拟':'拒绝本次模拟'}{row.payload.reason?` · ${row.payload.reason}`:''}</td><td>{row.payload.projected_fill?`${row.payload.projected_fill.quantity} / ${row.payload.projected_fill.price} / ${row.payload.projected_fill.fee}`:'无预计成交'}</td><td>{row.recorded_at}</td></tr>)}</tbody></table></div>:<p>此账户尚无事前逐单规则记录。</p>}<p>最多展示100条。授权仅对其冻结准备有效；已取消批次不能因历史允许判定而恢复成交。实盘始终关闭。</p></div>:null}</section>;
}
