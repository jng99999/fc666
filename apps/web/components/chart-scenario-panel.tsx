'use client';
import {useEffect,useMemo,useRef,useState} from 'react';
import type {PriceBar,PriceContext,PriceAnalysis} from '../lib/price-action';
import {calculatePlan,runChartStudies,summarizeExposure,type Direction,type TradingCosts,type StudyReport,type PlanLeg,type ChartReferenceLine} from '../lib/chart-scenarios';
import {DirectionalAccounts,directionalRequest,type DirectionalView} from '../lib/directional';
import WorkspaceSelect from './workspace-select';
import styles from './chart-scenario-panel.module.css';

const format=(value:number|null)=>value===null?'—':value.toLocaleString('zh-CN',{maximumFractionDigits:6});
const utc=(value:number)=>new Date(value).toISOString().replace('T',' ').slice(0,19)+' UTC';
type LegFields={direction:Direction;quantity:string;entry:string;stop:string;target:string};
const blankLeg=():LegFields=>({direction:'LONG',quantity:'',entry:'',stop:'',target:''});
function parse(value:string){if(!value.trim())return NaN;return Number(value);}
function parseLeg(value:LegFields):PlanLeg{return {direction:value.direction,quantity:parse(value.quantity),entry:parse(value.entry),stop:parse(value.stop),target:parse(value.target)};}
function instrumentOf(context:PriceContext):string|null {
 if(context.instrumentId==='XAU/USD'||context.instrumentId==='swissquote:spot:XAU-USD'||context.instrumentId==='swissquote:XAU/USD')return 'XAU/USD';
 const match=/^bitget:spot:(BTC|ETH|SOL|XRP|BNB|DOGE|ADA|AVAX)-USDT$/.exec(context.instrumentId);
 return match?match[1]+'USDT':null;
}

const noLines:ChartReferenceLine[]=[];
type LineCallback=(lines:ChartReferenceLine[])=>void;
// A changing parent callback must not retrigger parent state updates indefinitely.
function useChartReferences(lines:ChartReferenceLine[],callback:LineCallback|undefined){
 const callbackRef=useRef(callback);
 useEffect(()=>{callbackRef.current=callback;},[callback]);
 useEffect(()=>{callbackRef.current?.(lines);},[lines]);
 useEffect(()=>()=>{callbackRef.current?.([]);},[]);
}
export function planReferenceLines(legs:readonly PlanLeg[],costs:TradingCosts):ChartReferenceLine[]{
 return legs.flatMap((leg,index)=>{
  try{calculatePlan(leg,costs);}catch{return [];}
  const prefix=`研究计划 第${index+1}档`;
  return [{id:`research-${index}-entry`,label:`${prefix} 入场 · 不下单`,price:leg.entry,color:'#3976da'},{id:`research-${index}-stop`,label:`${prefix} 止损 · 不下单`,price:leg.stop,color:'#dc5270'},{id:`research-${index}-target`,label:`${prefix} 止盈 · 不下单`,price:leg.target,color:'#168567'}];
 });
}
export function accountReferenceLines(accounts:readonly DirectionalView[],instrument:string|null):ChartReferenceLine[]{
 if(!instrument)return [];
 return accounts.flatMap(account=>{
  if(account.config.instrument!==instrument||!account.position)return [];
  const position=account.position,values=[Number(position.quantity),Number(position.entry),Number(position.stop_loss),Number(position.take_profit)];
  if(!values.every(value=>Number.isFinite(value)&&value>0))return [];
  const prefix=`模拟账本 ${account.account_id.slice(-8)}`;
  return [{id:`account-${account.account_id}-entry`,label:`${prefix} 成本 · 非实时快照`,price:values[1],color:'#3976da'},{id:`account-${account.account_id}-stop`,label:`${prefix} 止损 · 非实时快照`,price:values[2],color:'#dc5270'},{id:`account-${account.account_id}-target`,label:`${prefix} 止盈 · 非实时快照`,price:values[3],color:'#168567'}];
 });
}

function Ledger({context,onAccountLevels}:{context:PriceContext;onAccountLevels?:LineCallback}){
 const [data,setData]=useState<DirectionalView[]|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[checked,setChecked]=useState<number|null>(null);
 const request=useRef<AbortController|null>(null);
 useEffect(()=>()=>{request.current?.abort();},[]);
 const instrument=instrumentOf(context),currency=instrument==='XAU/USD'?'USD':'USDT';
 const accountLines=useMemo(()=>data?accountReferenceLines(data,instrument):noLines,[data,instrument]);
 useChartReferences(accountLines,onAccountLevels);
 async function load(){request.current?.abort();const controller=new AbortController();request.current=controller;setBusy(true);setError('');setData(null);setChecked(null);try{const result=await directionalRequest('accounts',DirectionalAccounts,undefined,controller.signal);if(request.current!==controller||controller.signal.aborted)return;setData(result.accounts);setChecked(Date.now());}catch(error){if(request.current===controller&&!controller.signal.aborted)setError(error instanceof Error?error.message:'无法读取模拟账本。');}finally{if(request.current===controller&&!controller.signal.aborted)setBusy(false);}}
 const matched=data?.filter(account=>account.config.instrument===instrument)??[];
 const candidates=matched.filter(account=>account.position&&account.last_quote).map(account=>{const position=account.position!;return {direction:position.direction,quantity:Number(position.quantity),entry:Number(position.entry),equity:Number(account.equity),mark:(Number(account.last_quote!.bid)+Number(account.last_quote!.ask))/2};});
 let positions=candidates.filter(row=>[row.quantity,row.entry,row.equity,row.mark].every(Number.isFinite)&&row.quantity>0&&row.entry>0&&row.mark>0);
 try{summarizeExposure(positions,10);summarizeExposure(positions,-10);}catch{positions=[];}
 return <details className={styles.section}><summary>模拟持仓与压力分析 <span>只读账本</span></summary><div className={styles.body}>
  <p>仅汇总 {instrument??context.instrumentId} 的独立双向模拟账户。不同产品、USD 与 USDT 分开显示；现货旧账本请到<a href="/paper/realtime">实时模拟</a>查看，黄金原账户请到<a href="/gold">黄金</a>查看，不包含在此汇总。</p>
  <button type="button" disabled={busy||!instrument} onClick={()=>void load()}>{busy?'读取中…':data?'刷新只读账本':'读取模拟账本'}</button>
  {!instrument?<p>当前图表产品尚无可核对的双向账户映射，不合并其他产品。</p>:null}
  {error?<p role="alert" className={styles.error}>{error} {data?'下方仍为上次确认快照。':''}</p>:null}
  {checked?<small>读取时间 {utc(checked)} · 非实时推送，请刷新核对。</small>:null}
  {data&&!matched.length?<p role="status">此浏览器没有该产品的独立双向模拟账户。<a href="/paper/directional">查看或创建账户</a></p>:null}
  {matched.map(account=><article key={account.account_id} className={styles.account}><strong>{account.config.instrument} · {account.account_id.slice(-8)}</strong><small>{account.status==='STOPPED'?'已停新风险':'运行中'} · 更新 {account.updated_at}</small><dl className={styles.metrics}><div><dt>权益 · {currency}</dt><dd>{format(Number(account.equity))}</dd></div><div><dt>累计手续费</dt><dd>{format(Number(account.fees))}</dd></div><div><dt>已实现毛盈亏</dt><dd>{format(Number(account.gross_pnl))}</dd></div><div><dt>未实现盈亏</dt><dd>{format(Number(account.unrealized_pnl))}</dd></div></dl>{account.position?<p>{account.position.direction==='LONG'?'多头':'空头'} {account.position.quantity} · 成本 {account.position.entry}<br/>止损 {account.position.stop_loss} · 止盈 {account.position.take_profit}</p>:<p>无持仓</p>}{account.last_quote?<small>账本报价 {account.last_quote.at} · {account.last_quote.source}</small>:<small>缺少有效报价，不纳入压力估值。</small>}</article>)}
  {positions.length<candidates.length?<p role="alert" className={styles.error}>部分报价或账本数值无效，已排除其压力估值。</p>:null}
  {positions.length?(()=>{const base=summarizeExposure(positions,0);return <div className={styles.account}><h4>同产品持仓汇总 · {currency}</h4><dl className={styles.metrics}><div><dt>多头均价</dt><dd>{format(base.longAverage)}</dd></div><div><dt>空头均价</dt><dd>{format(base.shortAverage)}</dd></div><div><dt>净数量</dt><dd>{format(base.netQuantity)}</dd></div><div><dt>总名义敞口</dt><dd>{format(base.grossNotional)}</dd></div><div><dt>成本净额平衡价</dt><dd>{format(base.netBreakEven)}</dd></div></dl><p>平衡价不含已实现盈亏或退出成本；净数量为零时无单一平衡价。压力仅对有报价的当前持仓线性重估，不模拟期间止盈止损。</p><div className={styles.scenarios}>{[-10,-5,5,10].map(shock=>{const result=summarizeExposure(positions,shock);return <div key={shock}><span>价格 {shock>0?'+':''}{shock}%</span><strong>{format(result.shockPnl)} {currency}</strong></div>;})}</div></div>;})():null}
  <p>全额抵押的合成 1× 模拟，没有杠杆、维持保证金或强平模型，不能据此估算真实全仓强平价。本面板不会创建、停止或修改账户。</p>
 </div></details>;
}

type Props={bars:readonly PriceBar[];context:PriceContext;analysis:PriceAnalysis;onPlanLevels?:LineCallback;onAccountLevels?:LineCallback;showLedger?:boolean};
export default function ChartScenarioPanel(props:Props){return <ScenarioContent key={[props.context.instrumentId,props.context.source,props.context.timeframe].join(':')} {...props}/>;}
function ScenarioContent({bars,context,analysis,onPlanLevels,onAccountLevels,showLedger=true}:Props){
 const [legs,setLegs]=useState<LegFields[]>([blankLeg()]),[zoneId,setZoneId]=useState(''),[planNotice,setPlanNotice]=useState('');
 const [costFields,setCostFields]=useState({feeRate:'0.001',spread:'0',slippage:'0',fundingPerDay:'0'});
 const [quantity,setQuantity]=useState(''),[capital,setCapital]=useState('10000'),[report,setReport]=useState<{key:string;value:StudyReport}|null>(null),[studyError,setStudyError]=useState(''),[running,setRunning]=useState(false);
 const costs:TradingCosts=useMemo(()=>({feeRate:parse(costFields.feeRate),spread:parse(costFields.spread),slippage:parse(costFields.slippage),fundingPerDay:parse(costFields.fundingPerDay)}),[costFields]);
 const planLines=useMemo(()=>planReferenceLines(legs.map(parseLeg),costs),[legs,costs]);
 useChartReferences(planLines,onPlanLevels);
 const dataKey=[context.instrumentId,context.source,context.timeframe,bars.length,bars.slice(-500).map(bar=>[bar.time,bar.closedAt,bar.open,bar.high,bar.low,bar.close].join(',')).join('|'),costFields.feeRate,costFields.spread,costFields.slippage,costFields.fundingPerDay,quantity,capital].join(':');
 const zones=analysis.annotations.filter(zone=>['range','demand','supply','fvg','support','resistance','liquidity'].includes(zone.kind)&&zone.state!=='BROKEN'&&zone.state!=='EXPIRED');
 const outcomes=legs.map(leg=>{try{return {value:calculatePlan(parseLeg(leg),costs),error:''};}catch(error){return {value:null,error:error instanceof Error?error.message:'参数无效'};}});
 function updateLeg(index:number,key:keyof LegFields,value:string){setLegs(current=>current.map((leg,i)=>i===index?{...leg,[key]:value}:leg));}
 function seed(){const zone=zones.find(row=>row.id===zoneId),atr=analysis.atr;if(!zone||!atr||atr<=0){setPlanNotice('请选择有效区域，且需有足够闭合历史计算 ATR。');return;}const direction=zone.kind==='supply'||zone.kind==='resistance'?'SHORT':'LONG',entry=(zone.low+zone.high)/2,sign=direction==='LONG'?1:-1,stop=direction==='LONG'?zone.low-atr*.5:zone.high+atr*.5,target=entry+sign*Math.abs(entry-stop)*2;setLegs([{direction,quantity:'',entry:String(entry),stop:String(stop),target:String(target)}]);setPlanNotice('已生成一档候选：区域中点入场、边界外 0.5 ATR 止损、毛风险 2 倍止盈。请填写数量并复核，未发送订单。');}
 async function run(){setRunning(true);setStudyError('');try{await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));const value=runChartStudies(bars,{costs,quantity:parse(quantity),capital:parse(capital),now:Date.now()});setReport({key:dataKey,value});}catch(error){setStudyError(error instanceof Error?error.message:'研究失败');}finally{setRunning(false);}}
 return <section className={styles.root} aria-label="交易计划与历史研究"><header><h3>计划与验证</h3><span>研究模式 · 实盘关闭</span></header>
  <details className={styles.section}><summary>分档多空计划 <span>手动复核 · 不下单</span></summary><div className={styles.body}>
   <p>数量以基础币计，黄金以盎司计；金额使用产品报价币。以下成本是可编辑研究假设，并非已连接账户的实际收费。</p>
   <div className={styles.fields}>{Object.entries(costFields).map(([key,value])=><label key={key}>{({feeRate:'单边手续费率',spread:'完整点差 · 绝对价格',slippage:'单边滑点 · 绝对价格',fundingPerDay:'每日资金成本率 · 双向费用假设'} as Record<string,string>)[key]}<input aria-label={({feeRate:'单边手续费率',spread:'完整点差',slippage:'单边滑点',fundingPerDay:'每日资金成本率'} as Record<string,string>)[key]} type="number" min="0" step="any" value={value} onChange={event=>setCostFields(current=>({...current,[key]:event.target.value}))}/></label>)}</div>
   <p>费率 0.001 = 0.1%。点差按入场与退出各半计，滑点每侧均向不利方向；每日资金成本只在历史研究按持仓时间计费，不冒充实时永续资金费。</p>
   <div className={styles.fields}><label>候选区域<WorkspaceSelect label="计划候选区域" value={zoneId} onValueChange={setZoneId} placeholder="选择已确认区域" options={zones.map(zone=>({value:zone.id,label:`${zone.label} · ${format(zone.low)}–${format(zone.high)}`}))}/></label><button type="button" onClick={seed} disabled={!zoneId}>从区域生成候选</button></div>
   {planNotice?<p role="status">{planNotice}</p>:null}
   {legs.map((leg,index)=><article key={index} className={styles.account}><div className={styles.heading}><h4>第 {index+1} 档</h4>{legs.length>1?<button type="button" onClick={()=>setLegs(current=>current.filter((_,i)=>i!==index))}>移除此档</button>:null}</div><div className={styles.fields}><label>方向<WorkspaceSelect label={`第${index+1}档方向`} value={leg.direction} onValueChange={value=>updateLeg(index,'direction',value)} options={[{value:'LONG',label:'做多 LONG'},{value:'SHORT',label:'做空 SHORT'}]}/></label>{(['quantity','entry','stop','target'] as const).map(key=><label key={key}>{{quantity:'数量',entry:'入场价',stop:'止损价',target:'止盈价'}[key]}<input aria-label={`第${index+1}档${{quantity:'数量',entry:'入场价',stop:'止损价',target:'止盈价'}[key]}`} type="number" min="0" step="any" value={leg[key]} onChange={event=>updateLeg(index,key,event.target.value)}/></label>)}</div>{outcomes[index].value?<dl className={styles.metrics}><div><dt>成本后最大计划损失</dt><dd>{format(outcomes[index].value!.risk)}</dd></div><div><dt>成本后计划收益</dt><dd>{format(outcomes[index].value!.reward)}</dd></div><div><dt>净收益 / 风险</dt><dd>{format(outcomes[index].value!.ratio)}</dd></div><div><dt>含退出成本平衡价</dt><dd>{format(outcomes[index].value!.breakEven)}</dd></div></dl>:<p className={styles.error}>{outcomes[index].error}</p>}</article>)}
   <button type="button" disabled={legs.length>=4} onClick={()=>setLegs(current=>[...current,blankLeg()])}>添加一档（最多 4 档）</button>
   {outcomes.every(row=>row.value)?<p>各档止损都成交时合计计划损失 {format(outcomes.reduce((sum,row)=>sum+row.value!.risk,0))}。同时多空按独立档位估计，不代表当前单持仓模拟账户支持对锁。</p>:null}
   <p>保护价不是成交保证；跳空可能扩大损失。此处不计算杠杆或强平。</p>
  </div></details>
  <details className={styles.section}><summary>四策略历史对比 <span>当前图表 · 最多 500 柱</span></summary><div className={styles.body}>
   <p>采用上方费用假设。前序闭合柱计算信号，下一柱开盘模拟入场，ATR 1.5 倍止损、3 倍止盈；固定数量，不寻优。</p><div className={styles.fields}><label>每次研究数量<input type="number" min="0" step="any" value={quantity} onChange={event=>setQuantity(event.target.value)}/></label><label>独立研究资金<input type="number" min="0" step="any" value={capital} onChange={event=>setCapital(event.target.value)}/></label></div>
   <button type="button" disabled={running} onClick={()=>void run()}>{running?'计算中…':'运行四策略对比'}</button>{studyError?<p role="alert" className={styles.error}>{studyError}</p>:null}
   {report&&report.key!==dataKey?<p role="status">图表数据或研究参数已改变，上次结果已隐藏，请重新运行。</p>:null}
   {report&&report.key===dataKey?<><p>{report.value.bars} 根闭合柱 · {utc(report.value.from)} 至 {utc(report.value.to)}<br/>后期留出开始 {utc(report.value.splitAt)} · 未纳入 {report.value.excluded} 根（未闭合或超过 500 根）。</p><div className={styles.results}>{report.value.results.map(result=><article className={styles.account} key={result.kind}><h4>{result.label}</h4><dl className={styles.metrics}><div><dt>模拟交易数</dt><dd>{result.trades.length}</dd></div><div><dt>净盈亏 / 收益率</dt><dd>{format(result.net)} / {format(result.returnPercent)}%</dd></div><div><dt>已实现权益最大回撤</dt><dd>{format(result.maxDrawdown)}</dd></div><div><dt>模型总成本</dt><dd>{format(result.cost)}</dd></div><div><dt>早期 / 后期净盈亏</dt><dd>{format(result.earlyNet)} / {format(result.lateNet)}</dd></div><div><dt>同柱双触发 · 先止损</dt><dd>{result.ambiguous}</dd></div></dl>{!result.trades.length?<p>本段历史没有满足规则的入场，不伪造交易。</p>:<details><summary>查看最近 5 笔模拟记录</summary>{result.trades.slice(-5).map((trade,index)=><p key={index}>{trade.direction} · {utc(trade.entryAt)}<br/>{format(trade.entry)} → {format(trade.exit)} · 净额 {format(trade.net)} · {trade.reason} · {trade.sample}</p>)}</details>}</article>)}</div>{report.value.warnings.map(warning=><p className={styles.note} key={warning}>{warning}</p>)}<p className={styles.note}>回撤仅按每笔退出的已实现权益计算，不包含持仓中浮亏，不能替代逐笔净值回测。</p></>:<p>尚未运行。短窗口结果不能证明长期有效；资金不足时不生成该笔研究交易。</p>}
  </div></details>
  {showLedger?<Ledger key={context.instrumentId} context={context} onAccountLevels={onAccountLevels}/>:<p>历史回放不加载当前账户快照，避免混入回放时点之后的信息。</p>}
 </section>;
}
