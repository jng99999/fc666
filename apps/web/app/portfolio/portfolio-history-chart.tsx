'use client';
import Link from 'next/link';
import {useEffect,useRef,useState} from 'react';
import {z} from 'zod';
import WorkspaceSelect from '../../components/workspace-select';

const decimal=z.string().regex(/^-?\d+(\.\d+)?([Ee][+-]?\d+)?$/).refine(value=>Number.isFinite(Number(value)));
const Scene=z.object({scenario_id:z.uuid(),definition:z.object({name:z.string(),session_ids:z.array(z.uuid()),limits:z.object({max_gross_weight:decimal,max_asset_weight:decimal})})});
const Scenes=z.object({items:z.array(Scene),next_cursor:z.string().nullable(),trading_enabled:z.literal(false)});
const Sample=z.object({version:z.literal('paper-portfolio-sampled-v1'),scenario_id:z.uuid(),trading_enabled:z.literal(false),window:z.object({has_earlier:z.boolean(),points:z.number().int()}),points:z.array(z.object({snapshot_id:z.uuid(),price_as_of:z.iso.datetime({offset:true}),equity:decimal.nullable(),segment_id:z.string().nullable(),plot_index:z.number().int()})),inputs_sha256:z.string().regex(/^[a-f0-9]{64}$/)});
type Props={ids:string[];gross:string;asset:string;refresh:number;hidden:boolean};
type Range='1W'|'1M'|'3M';
const DAYS:Record<Range,number>={'1W':7,'1M':30,'3M':90};
const shortDate=(value:string)=>new Date(value).toLocaleDateString('zh-CN',{timeZone:'UTC',month:'2-digit',day:'2-digit'});

export default function PortfolioHistoryChart({ids,gross,asset,refresh,hidden}:Props){
 const [scenes,setScenes]=useState<z.infer<typeof Scene>[]>([]),[chosen,setChosen]=useState(''),[sample,setSample]=useState<z.infer<typeof Sample>|null>(null);
 const [loading,setLoading]=useState(false),[error,setError]=useState(''),[range,setRange]=useState<Range>('1W'),[retry,setRetry]=useState(0);
 const plot=useRef<HTMLDivElement>(null);
 const [bounds,setBounds]=useState({width:900,height:150});
 const idsKey=JSON.stringify(ids);
 useEffect(()=>{
  const controller=new AbortController();setScenes([]);setSample(null);setError('');
  if(!ids.length){setLoading(false);return()=>controller.abort();}
  setLoading(true);
  const signal=AbortSignal.any([controller.signal,AbortSignal.timeout(20000)]);
  void(async()=>{let cursor:string|null=null;const seen=new Set<string>();const all:z.infer<typeof Scene>[]=[];
   for(let page=0;page<5;page++){
    const query=new URLSearchParams({limit:'20'});if(cursor)query.set('cursor',cursor);
    const response=await fetch('/api/portfolio/scenarios?'+query,{cache:'no-store',signal});
    if(!response.ok)throw Error('已保存场景暂不可用 ('+response.status+')');
    const data=Scenes.parse(await response.json());all.push(...data.items);cursor=data.next_cursor;
    if(!cursor)break;if(seen.has(cursor))throw Error('场景分页未前进，请重新读取');seen.add(cursor);
    if(page===4)throw Error('场景数量超出当前可核对范围，请在场景记录中查看');
   }
   if(!controller.signal.aborted)setScenes(all);
  })().catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'历史读取失败');}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[idsKey,refresh,retry]); // idsKey binds the exact ordered account selection.
 const matching=scenes.filter(scene=>JSON.stringify(scene.definition.session_ids)===idsKey&&Number(scene.definition.limits.max_gross_weight)===Number(gross)&&Number(scene.definition.limits.max_asset_weight)===Number(asset));
 const active=matching.find(scene=>scene.scenario_id===chosen)??matching[0];
 const activeId=active?.scenario_id;
 useEffect(()=>{
  const controller=new AbortController();setSample(null);
  if(!activeId)return()=>controller.abort();
  setLoading(true);setError('');
  void fetch('/api/portfolio/scenarios/'+activeId+'/sampled?limit=8',{cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)])}).then(async response=>{if(!response.ok)throw Error('保存观察暂不可用 ('+response.status+')');return Sample.parse(await response.json());}).then(data=>{if(data.scenario_id!==activeId)throw Error('返回的场景不一致');if(!controller.signal.aborted)setSample(data);}).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'历史读取失败');}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return()=>controller.abort();
 },[activeId,refresh,retry]);
 useEffect(()=>{
  const node=plot.current;if(!node)return;
  const resize=()=>setBounds({width:Math.max(200,node.clientWidth),height:Math.max(90,node.clientHeight)});
  resize();const observer=new ResizeObserver(resize);observer.observe(node);return()=>observer.disconnect();
 },[sample,hidden,loading,error]);
 const latest=Math.max(0,...(sample?.points.map(point=>Date.parse(point.price_as_of))??[]));
 const points=sample?.points.filter(point=>Date.parse(point.price_as_of)>=latest-DAYS[range]*86400000)??[];
 const values=points.filter(point=>point.equity!==null).map(point=>Number(point.equity));
 const low=values.length?Math.min(...values):0,high=values.length?Math.max(...values):0;
 const chartRight=bounds.width-76,chartBottom=bounds.height-26;
 const x=(index:number)=>6+index/Math.max(points.length-1,1)*(chartRight-6);
 const y=(equity:string)=>high===low?(12+chartBottom)/2:chartBottom-(Number(equity)-low)/(high-low)*(chartBottom-12);
 const segmentIds=[...new Set(points.flatMap(point=>point.segment_id===null?[]:[point.segment_id]))];
 const lastAvailable=points.findLast(point=>point.equity!==null);
 return <div className="account-history-chart">
  <div className="account-chart-heading"><h2>资产曲线 <span>USDT · 已保存观察</span></h2><div className="account-range" aria-label="资产曲线时间范围">{(['1W','1M','3M'] as const).map(key=><button key={key} type="button" disabled={!sample?.points.length} aria-pressed={range===key} onClick={()=>setRange(key)}>{key==='1W'?'1周':key==='1M'?'1月':'3月'}</button>)}</div></div>
  {matching.length>1?<label className="account-chart-scenario"><span style={{whiteSpace:'nowrap'}}>场景</span><WorkspaceSelect label="资产曲线场景" value={activeId??''} onValueChange={setChosen} options={matching.map(scene=>({value:scene.scenario_id,label:scene.definition.name}))}/></label>:null}
  {hidden?<div className="account-chart-empty account-private-chart"><span>资产信息已隐藏</span></div>:loading?<div className="account-chart-empty" role="status"><span>正在读取已保存观察…</span></div>:error?<div className="account-chart-empty"><p role="alert">{error}</p><button type="button" onClick={()=>setRetry(value=>value+1)}>重新读取</button></div>:!points.length?<div className="account-chart-empty"><span>{active?'这个场景尚无已保存观察':'尚无同账户、同阈值的场景记录'}</span><Link href="/portfolio/history">创建场景并保存估值 <span aria-hidden="true">→</span></Link></div>:<>
   <div className="account-chart-plot" ref={plot}><span className="account-chart-source">已保存观察 · USDT</span><svg viewBox={'0 0 '+bounds.width+' '+bounds.height} className="account-equity-svg" role="img" aria-label="保存顺序中的模拟权益观察，仅同一可比区间连接" aria-describedby="account-history-note"><title>{active?.definition.name} · {points.length} 个实际保存观察，间距不代表经过时间</title>
    {[12,(12+chartBottom)/2,chartBottom].map(at=><line key={at} x1="0" x2={chartRight} y1={at} y2={at} stroke="#e7eef8" strokeDasharray="2 3"/>)}
    {segmentIds.map(segment=>{const members=points.map((point,index)=>({point,index})).filter(({point})=>point.segment_id===segment&&point.equity!==null);return members.length>1?<g key={segment}><polygon points={x(members[0].index)+','+chartBottom+' '+members.map(({point,index})=>x(index)+','+y(point.equity!)).join(' ')+' '+x(members[members.length-1].index)+','+chartBottom} fill="#2677ff16"/><polyline points={members.map(({point,index})=>x(index)+','+y(point.equity!)).join(' ')} fill="none" stroke="#2677ff" strokeWidth="1.8"/></g>:null;})}
    {points.map((point,index)=>point.equity===null?<text key={point.snapshot_id} className="account-svg-missing" x={x(index)} y={chartBottom} fill="#ba6a21" textAnchor="middle">×<title>不可汇总，未计作零权益</title></text>:<circle key={point.snapshot_id} cx={x(index)} cy={y(point.equity)} r="2.5" fill="#2677ff" tabIndex={0}><title>{point.price_as_of+' · '+point.equity+' USDT'}</title></circle>)}
    {[0,Math.floor((points.length-1)/2),points.length-1].filter((index,position,all)=>all.indexOf(index)===position).map(index=><text key={index} x={x(index)} y={bounds.height-5} fill="#7d96b7" textAnchor={index===0?'start':index===points.length-1?'end':'middle'}>{shortDate(points[index].price_as_of)}</text>)}
    {values.length&&lastAvailable?<><text x={bounds.width-5} y="15" fill="#7d96b7" textAnchor="end">{high.toLocaleString('zh-CN',{maximumFractionDigits:2})}</text>{high!==low?<text x={bounds.width-5} y={chartBottom+3} fill="#7d96b7" textAnchor="end">{low.toLocaleString('zh-CN',{maximumFractionDigits:2})}</text>:null}<rect x={chartRight+4} y={y(lastAvailable.equity!)-9} width="72" height="17" fill="#2677ff"/><text x={bounds.width-4} y={y(lastAvailable.equity!)+3} fill="#fff" textAnchor="end">{Number(lastAvailable.equity).toLocaleString('zh-CN',{maximumFractionDigits:2})}</text></>:<text x={chartRight/2} y={chartBottom/2-5} className="account-svg-note" fill="#7186a3" textAnchor="middle"><tspan x={chartRight/2}>保存观察均不可汇总</tspan><tspan x={chartRight/2} dy="16">未计作零权益</tspan></text>}
   </svg></div><p className="account-chart-note" id="account-history-note">{active?.definition.name} · 最近最多 8 个保存点，按保存顺序排列；{segmentIds.length>1?'区间有中断，不跨段连线。':'仅连接可比观察，不代表中间权益。'} {sample?.window.has_earlier?'更早记录请到场景记录查看。':''}</p>
  </>}
 </div>;
}
