'use client';
import {useCallback,useEffect,useMemo,useRef,useState,type ReactNode,type PointerEvent} from 'react';
import type {ChartCoordinates} from '../lib/chart-coordinates';
import {layoutChartLabels,type ChartOverlayLabel} from '../lib/chart-label-layout';
import {analyzePriceAction,type PriceAnalysis,type PriceBar,type PriceContext,type PriceAnnotation} from '../lib/price-action';
import {decodeDrawings,drawingCount,drawingLabels,drawingScope,drawingStorageKey,encodeDrawings,snapDrawingPoint,validDrawing,type ChartDrawing,type DrawingKind,type DrawingPoint} from '../lib/chart-drawings';
import ChartScenarioPanel from './chart-scenario-panel';
import ChartDerivativesContext from './chart-derivatives-context';
import type {ChartReferenceLine} from '../lib/chart-scenarios';
import WorkspaceSelect from './workspace-select';
import ChartGesturePlane from './chart-gesture-plane';
import {chartLocalPoint,fitImmersiveChart,rotatedChartFrame,mobileExchangeQuery} from '../lib/chart-landscape';
import {chartTrendGeometry} from '../lib/chart-trend-geometry';
import {chartPatterns,chartPatternStates,type ChartPattern} from '../lib/chart-patterns';
import styles from './chart-workbench.module.css';
import AnalysisDialog from '../app/analysis/analysis-dialog';
export type MobileChartTools={open:boolean;close:()=>void;automatic?:boolean;setAutomatic?:(value:boolean)=>void};

type Props={bars:readonly PriceBar[];context:PriceContext;coordinates:ChartCoordinates|null;children:ReactNode;historical?:boolean;mobileTools?:MobileChartTools};
const groups={structure:['support','resistance','trend','channel','bos','choch'],zones:['range','supply','demand','fvg'],liquidity:['liquidity'],sessions:['session','level'],retracements:['fibonacci']} as const;
const groupNames={structure:'结构与趋势',zones:'箱体与供需',liquidity:'流动性',sessions:'时段与前日/周',retracements:'回撤参考'};
const states={ACTIVE:'有效',TOUCHED:'已触碰',MITIGATED:'已回补',BROKEN:'已突破',EXPIRED:'已过期'};
const fmt=(n:number)=>n.toLocaleString('zh-CN',{maximumFractionDigits:6});
const utc=(n:number)=>new Date(n).toISOString().replace('T',' ').replace('.000Z',' UTC');
const color=(a:PriceAnnotation)=>['BROKEN','EXPIRED','MITIGATED'].includes(a.state)?'#94a3b8':a.direction==='up'?'#139b79':a.direction==='down'?'#d84c70':'#3b7ee8';
const shortLabel=(a:PriceAnnotation)=>({support:'支撑候选',resistance:'阻力候选',range:'震荡箱体',trend:'趋势线',channel:'趋势通道',demand:'需求 / OB候选',supply:'供应 / OB候选',fvg:'FVG',bos:'BOS',choch:'CHoCH',session:a.label,level:a.label,fibonacci:'回撤参考',liquidity:'流动性'}[a.kind])+(a.direction==='up'?' ↑':a.direction==='down'?' ↓':'');

export default function ChartWorkbench(props:Props){return <WorkbenchState {...props}/>;}
function WorkbenchState({bars,context,coordinates,children,historical=false,mobileTools}:Props){
 const [mobile,setMobile]=useState(false);
 useEffect(()=>{const media=window.matchMedia(mobileExchangeQuery),update=()=>setMobile(media.matches);update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
 const study=useMemo(()=>{try{return {analysis:analyzePriceAction(bars,context),error:''};}catch(error){const analysis:PriceAnalysis={annotations:[],regime:'UNAVAILABLE',atr:null,last:null,confirmedThrough:null,warnings:[],volumeProfile:{kind:'UNAVAILABLE',bins:[],poc:null,vah:null,val:null,totalVolume:0,reason:'数据未通过校验，停止区域计算。'}};return {analysis,error:error instanceof Error?error.message:'结构数据无法校验。'};}},[bars,context.instrumentId,context.source,context.timeframe,context.volumeKind]);
 const {analysis}=study;
 const patterns=useMemo(()=>mobile&&!study.error?chartPatterns(bars,analysis):[],[mobile,bars,analysis,study.error]);
 const [showPatterns,setShowPatterns]=useState(true);
 const [layers,setLayers]=useState({structure:true,zones:true,liquidity:false,sessions:false,retracements:false}),[desktopAutomatic,setAutomatic]=useState(true),[inactive,setInactive]=useState(false),[profile,setProfile]=useState(false);
 const compact=Boolean(mobileTools),[phoneAutomatic,setPhoneAutomatic]=useState(false);
 const automatic=compact?(mobileTools?.automatic??phoneAutomatic):desktopAutomatic;
 function toggleAutomatic(){const next=!automatic;if(compact){if(mobileTools?.setAutomatic)mobileTools.setAutomatic(next);else setPhoneAutomatic(next);}else setAutomatic(next);}
 const [channelDirection,setChannelDirection]=useState<'all'|'up'|'down'>('all');
 const [tool,setTool]=useState<DrawingKind|'cursor'>('cursor'),[snap,setSnap]=useState(true),[draft,setDraft]=useState<DrawingPoint[]>([]),[selection,setSelection]=useState('');
 const scope=drawingScope(context);
 const currentScope=useRef(scope),importRevision=useRef(0);currentScope.current=scope;
 const [planLevels,setPlanLevels]=useState<{scope:string;lines:ChartReferenceLine[]}>({scope,lines:[]}),[accountLevels,setAccountLevels]=useState<{scope:string;lines:ChartReferenceLine[]}>({scope,lines:[]});
 const receivePlan=useCallback((lines:ChartReferenceLine[])=>setPlanLevels({scope,lines}),[scope]);
 const receiveAccounts=useCallback((lines:ChartReferenceLine[])=>setAccountLevels({scope,lines}),[scope]);
 const references=[...(planLevels.scope===scope?planLevels.lines:[]),...(!historical&&accountLevels.scope===scope?accountLevels.lines:[])];
 const [history,setHistory]=useState<{scope:string;past:ChartDrawing[][];present:ChartDrawing[];future:ChartDrawing[][]}>({scope,past:[],present:[],future:[]});
 const [loadedScope,setLoadedScope]=useState(''),[blockedRead,setBlockedRead]=useState(false),[notice,setNotice]=useState(''),[external,setExternal]=useState<string|null>(null),[revision,setRevision]=useState(0),[editError,setEditError]=useState('');
 const importInput=useRef<HTMLInputElement>(null),surface=useRef<HTMLDivElement>(null);
 const ready=loadedScope===scope&&history.scope===scope,drawings=history.scope===scope?history.present:[],key=drawingStorageKey(context),lastTime=bars.at(-1)?.time??-1;
 useEffect(()=>{
  importRevision.current++;setHistory({scope,past:[],present:[],future:[]});setDraft([]);setTool('cursor');setSelection('');setExternal(null);setNotice('');setBlockedRead(false);
  try{const saved=localStorage.getItem(key);if(saved)setHistory({scope,past:[],present:decodeDrawings(saved,context),future:[]});}
  catch{setBlockedRead(true);setNotice('本机绘图无法读取；原始存储未删除。添加或导入图形前不会覆盖原记录。');}
  setLoadedScope(scope);
  const changed=(event:StorageEvent)=>{if(event.key===key&&event.newValue)setExternal(event.newValue);};
  window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);
 // Scope is loaded before persistence; chart hosts stay mounted when a market changes.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[key]);
 useEffect(()=>{if(!ready||external||blockedRead)return;try{localStorage.setItem(key,encodeDrawings(drawings,context));}catch{setNotice('浏览器未允许保存绘图；本次仍可使用，请导出 JSON 备份。');}
 // Context identity may change on every chart inspection; persist only actual drawings.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[history.present,key,ready,external,blockedRead]);
 useEffect(()=>{if(!coordinates)return;return coordinates.subscribe(()=>setRevision(value=>value+1));},[coordinates]);
 useEffect(()=>{setRevision(value=>value+1);},[bars]);
 const kinds=new Set(Object.keys(layers).filter(k=>layers[k as keyof typeof layers]).flatMap(k=>[...groups[k as keyof typeof groups]]));
 const candidates=automatic?analysis.annotations.filter(a=>kinds.has(a.kind as never)&&(inactive||!['BROKEN','EXPIRED','MITIGATED'].includes(a.state))&&a.confirmedAt<=(bars.at(-1)?.closedAt??0)&&(!mobile||!['trend','channel'].includes(a.kind)||channelDirection==='all'||a.direction===channelDirection)):[];
 // One latest object per type prevents stacking dozens of overlapping gaps on a phone.
 const latest=new Map(candidates.map(a=>[mobile&&['trend','channel'].includes(a.kind)?`${a.kind}:${a.direction}`:a.kind,a]));
 const ordered=[...latest.values()].sort((a,b)=>a.confirmedAt-b.confirmedAt),trends=ordered.filter(a=>['trend','channel'].includes(a.kind));
 const visible=mobile?[...trends,...ordered.filter(a=>!['trend','channel'].includes(a.kind)).slice(-Math.max(0,12-trends.length))].sort((a,b)=>a.confirmedAt-b.confirmedAt):ordered.slice(-12);
 const chosen=candidates.find(a=>a.id===selection);if(chosen&&!visible.includes(chosen))visible.push(chosen);
 const manual=drawings.filter(d=>d.points.every(p=>p.time<=lastTime&&p.time>=(bars[0]?.time??0)));
 const selectedManual=drawings.find(d=>d.id===selection),selectedAuto=analysis.annotations.find(a=>a.id===selection);
 const size=coordinates?.size()??{width:0,height:0};
 // revision makes coordinate projection follow actual chart scroll, scale and pane resize.
 void revision;
 const labels:ChartOverlayLabel[]=[];
 if(coordinates){
  visible.forEach((a,i)=>{
   if(a.id!==selection&&i<visible.length-3&&(!mobile||!['trend','channel'].includes(a.kind)))return;
   const shape=mobile?chartTrendGeometry(a,bars[0]?.time??a.start,lastTime):a;
   const y1=coordinates.priceToY(shape.high),y2=coordinates.priceToY(shape.low),x1=coordinates.timeToX(shape.start),x2=coordinates.timeToX(shape.end);
   const left=x1??(a.start<(bars[0]?.time??0)?0:null),right=x2??size.width;
   if(y1===null||y2===null||left===null||right<0||left>size.width||Math.max(y1,y2)<0||Math.min(y1,y2)>size.height)return;
   labels.push({id:a.id,selectionId:a.id,text:shortLabel(a),title:`${a.label} · ${states[a.state]} · ${a.explanation}`,x:Math.max(0,left),y:Math.max(0,Math.min(y1,y2)),color:color(a),priority:a.id===selection?100:mobile&&a.kind==='channel'?80:60+i/100});
  });
  if(automatic&&showPatterns)for(const pattern of patterns){const point=xy(pattern.points[2]);if(point)labels.push({id:pattern.id,text:pattern.kind==='DOUBLE_TOP'?'双顶 M 候选':'双底 W 候选',title:chartPatternStates[pattern.state],x:point.x,y:point.y,color:'#bd861e',priority:85});}
  if(profile&&analysis.volumeProfile.kind==='OHLCV_ESTIMATE')for(const [name,value] of [['POC',analysis.volumeProfile.poc],['VAH',analysis.volumeProfile.vah],['VAL',analysis.volumeProfile.val]] as const){
   const y=value===null?null:coordinates.priceToY(value);if(y!==null)labels.push({id:`profile:${name}`,text:name,title:`${name} · OHLCV 估算 · ${value===null?'':fmt(value)}`,x:2,y,color:'#8b62be',priority:40});
  }
  for(const d of manual){
   const p=xy(d.points[0]);if(!p)continue;
   labels.push({id:`drawing:${d.id}`,selectionId:d.id,text:d.label+(d.locked?' · 锁定':''),title:`手动 · ${d.label}`,x:p.x,y:p.y,color:d.id===selection?'#f09325':'#7256ca',priority:d.id===selection?100:30});
   if(d.id===selection&&d.kind==='fibonacci')for(const ratio of [0,.382,.5,.618,1,1.272,1.618]){
    const price=d.points[0].price+(d.points[1].price-d.points[0].price)*ratio,y=coordinates.priceToY(price);
    if(y!==null)labels.push({id:`${d.id}:${ratio}`,text:`${ratio} · ${fmt(price)}`,title:`回撤参考 ${ratio} · ${fmt(price)}`,x:p.x,y,color:'#f09325',priority:35});
   }
  }
  for(const line of references.slice(0,24)){
   const y=coordinates.priceToY(line.price);if(y===null)continue;
   labels.push({id:`reference:${line.id}`,text:`${line.label.replace('研究计划 第','研究 ').replace('档','').replace(' · 不下单','').replace(' · 非实时快照','')} · ${fmt(line.price)}`,title:`${line.label} · ${fmt(line.price)}`,x:2,y,color:line.color,priority:50});
  }
 }
 const placedLabels=layoutChartLabels(labels,size);
 function commit(next:ChartDrawing[]){setHistory(h=>({scope,past:[...h.past,h.present].slice(-40),present:next,future:[]}));setBlockedRead(false);setEditError('');}
 function selectTool(next:DrawingKind|'cursor'){
  if(next!=='cursor')mobileTools?.close();
  setTool(next);setDraft([]);setNotice(next==='cursor'?'':next==='channel'?'依次点击趋势起点、终点，再点通道另一侧。':`在主图点选 ${drawingCount[next]} 个价格坐标；Esc 取消。`);
  if(next!=='cursor')requestAnimationFrame(()=>{const target=surface.current;if(!target)return;const body=target.closest<HTMLElement>('.analysis-modal-body'),frame=target.closest('[data-chart-frame]');if(mobile&&body&&frame){const dialog=body.closest('dialog');if(dialog){dialog.scrollTop=0;dialog.scrollLeft=0;}const rect=target.getBoundingClientRect(),viewport=body.getBoundingClientRect();body.scrollTop=compact?0:body.scrollTop+(rotatedChartFrame(target)?viewport.right-rect.right:rect.top-viewport.top);const chart=target.querySelector<HTMLElement>('.quote-chart-host');if(chart)fitImmersiveChart(chart);}else target.scrollIntoView({block:'start',behavior:'auto'});if(mobile)target.querySelector<SVGSVGElement>('[data-testid="chart-drawing-overlay"]')?.focus({preventScroll:true});});
 }
 function point(event:PointerEvent<SVGSVGElement>){
  if(tool==='cursor'||!coordinates||!bars.length||study.error)return;
  const {x,y}=chartLocalPoint(event.currentTarget,event.clientX,event.clientY);
  if(x<0||y<0||x>size.width||y>size.height)return;
  const time=coordinates.xToTime(x),price=coordinates.yToPrice(y);if(time===null||price===null||!Number.isFinite(price)||price<=0)return;
  const points=[...draft,snapDrawingPoint({time,price},bars,snap)];
  if(points.length<drawingCount[tool]){setDraft(points);return;}
  const drawing:ChartDrawing={id:crypto.randomUUID(),kind:tool,points,locked:false,label:drawingLabels[tool]};
  if(!validDrawing(drawing)){setNotice('两个锚点需有不同时间；箱体和回撤还需有不同价格，请重新点选。');setDraft([]);return;}
  if(drawings.length>=100){setNotice('每个产品与周期最多保存 100 个图形，请先删除不需要的绘图。');return;}
  commit([...drawings,drawing]);setSelection(drawing.id);setTool('cursor');setDraft([]);setNotice('绘图已添加；仅保存到本机，不会提交交易。');
 }
 function changePoint(index:number,field:'price'|'time',value:string){
  if(!selectedManual||selectedManual.locked)return;
  const next={...selectedManual,points:selectedManual.points.map((p,i)=>i===index?{...p,[field]:field==='time'?Date.parse(value):Number(value)}:p)};
  if(!validDrawing(next)){setEditError('坐标无效：价格须为正数、时间须有效，两个锚点不能相同。');return;}
  commit(drawings.map(d=>d.id===next.id?next:d));
 }
 function download(){const url=URL.createObjectURL(new Blob([encodeDrawings(drawings,context)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`fc666-drawings-${context.instrumentId.replace(/[^a-z0-9]/gi,'-')}-${context.timeframe}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),0);}
 function load(text:string){try{const next=decodeDrawings(text,context);commit(next);setExternal(null);setSelection('');setNotice('已载入当前产品、来源与周期的绘图。');}catch{setNotice('载入失败：只接受当前产品、来源、周期的有效 FC666 绘图文件。');}}
 function xy(p:DrawingPoint){if(!coordinates)return null;const x=coordinates.timeToX(p.time),y=coordinates.priceToY(p.price);return x===null||y===null?null:{x,y};}
 function drawAutomatic(a:PriceAnnotation){
  if(!coordinates)return null;
  const shape=mobile?chartTrendGeometry(a,bars[0]?.time??a.start,lastTime):a,y1=coordinates.priceToY(shape.high),y2=coordinates.priceToY(shape.low),x1=coordinates.timeToX(shape.start),x2=coordinates.timeToX(shape.end);
  const left=x1??(shape.start<(bars[0]?.time??0)?0:null),right=x2??size.width;
  if(y1===null||y2===null||left===null||right<0||left>size.width||Math.max(y1,y2)<0||Math.min(y1,y2)>size.height)return null;
  const stroke=color(a),selected=a.id===selection,points=shape.points?.map(p=>({x:coordinates.timeToX(p.time),y:coordinates.priceToY(p.price)})),width=selected?2:1.2;
  let geometry:ReactNode;
  if(points?.length&&points.every(p=>p.x!==null&&p.y!==null)){
   if(a.kind==='channel'&&points.length===4){const [p,q,r,s]=points as {x:number;y:number}[];geometry=mobile?<><path d={`M${p.x},${p.y}L${q.x},${q.y}L${s.x},${s.y}L${r.x},${r.y}Z`} fill={stroke+'09'} stroke="none"/><line x1={p.x} y1={p.y} x2={q.x} y2={q.y} stroke={stroke} strokeWidth={width}/><line x1={r.x} y1={r.y} x2={s.x} y2={s.y} stroke={stroke} strokeWidth={width}/><line data-channel-midline x1={(p.x+r.x)/2} y1={(p.y+r.y)/2} x2={(q.x+s.x)/2} y2={(q.y+s.y)/2} stroke={stroke} strokeWidth={1} strokeDasharray="5 4"/></>:<path d={`M${p.x},${p.y} L${q.x},${q.y} M${r.x},${r.y} L${s.x},${s.y}`} stroke={stroke} fill="none" strokeWidth={width}/>;}
   else geometry=<polyline points={points.map(p=>`${p.x},${p.y}`).join(' ')} stroke={stroke} fill="none" strokeWidth={width}/>;
  }else geometry=Math.abs(y2-y1)<1?<line x1={Math.max(0,left)} x2={right} y1={y1} y2={y1} stroke={stroke} strokeWidth={selected?2:1} strokeDasharray="4 3"/>:<rect x={Math.max(0,left)} y={Math.min(y1,y2)} width={Math.max(1,right-Math.max(0,left))} height={Math.abs(y2-y1)} fill={stroke+(selected?'20':'0a')} stroke={stroke} strokeWidth={selected?2:1} strokeDasharray={a.state==='ACTIVE'?undefined:'4 3'}/>;
  return <g key={a.id} data-annotation-id={a.id} data-channel-direction={a.kind==='channel'?a.direction:undefined} data-selected={selected} className={styles.annotation} onClick={e=>{if(tool==='cursor'){e.stopPropagation();setSelection(a.id);}}}><title>{a.label} · {states[a.state]} · {a.explanation}</title>{geometry}</g>;
 }
 function drawManual(d:ChartDrawing){
  const ps=d.points.map(xy);if(ps.some(p=>!p))return null;const a=ps[0]!,b=ps[1],stroke=d.id===selection?'#f09325':'#7256ca';
  const common={stroke,strokeWidth:d.id===selection?2:1.3,fill:'none',vectorEffect:'non-scaling-stroke' as const};
  let shape:ReactNode;
  if(d.kind==='horizontal')shape=<line {...common} x1={0} x2={size.width} y1={a.y} y2={a.y}/>;
  else if(d.kind==='box')shape=<rect {...common} fill={stroke+'12'} x={Math.min(a.x,b!.x)} y={Math.min(a.y,b!.y)} width={Math.abs(b!.x-a.x)} height={Math.abs(b!.y-a.y)}/>;
  else if(d.kind==='fibonacci')shape=<>{[0,.382,.5,.618,1,1.272,1.618].map(r=>{const price=d.points[0].price+(d.points[1].price-d.points[0].price)*r,y=coordinates!.priceToY(price);return y===null?null:<line key={r} {...common} strokeDasharray={r===0||r===1?undefined:'4 3'} x1={Math.min(a.x,b!.x)} x2={Math.max(a.x,b!.x)} y1={y} y2={y}/>;})}</>;
  else if(d.kind==='channel'){const c=ps[2]!,dy=c.y-(a.y+(b!.y-a.y)*(c.x-a.x)/(b!.x-a.x));shape=<><path {...common} fill={stroke+'0c'} d={`M${a.x},${a.y} L${b!.x},${b!.y} L${b!.x},${b!.y+dy} L${a.x},${a.y+dy}Z`}/><line {...common} strokeDasharray="4 3" x1={a.x} y1={a.y+dy/2} x2={b!.x} y2={b!.y+dy/2}/></>;}
  else shape=<line {...common} x1={a.x} y1={a.y} x2={b!.x} y2={b!.y}/>;
  return <g key={d.id} data-drawing-id={d.id} className={styles.manual} onClick={e=>{if(tool==='cursor'){e.stopPropagation();setSelection(d.id);}}}><title>{d.label}{d.locked?' · 已锁定':''}</title>{shape}{d.id===selection?ps.map((p,i)=><circle key={i} cx={p!.x} cy={p!.y} r={4} fill={stroke}/>):null}</g>;
 }
 function drawPattern(pattern:ChartPattern){
  if(!coordinates)return null;const points=pattern.points.map(xy);if(points.some(p=>!p))return null;
  const left=points[1]!.x,right=coordinates.timeToX(pattern.through),neck=coordinates.priceToY(pattern.neckline),target=coordinates.priceToY(pattern.target);
  return <g key={pattern.id} data-chart-pattern={pattern.kind}><title>{chartPatternStates[pattern.state]} · 仅历史形态与条件测算</title><polyline points={points.map(p=>`${p!.x},${p!.y}`).join(' ')} fill="none" stroke="#d49a2a" strokeWidth={1.5}/>{points.map((p,i)=><circle key={i} cx={p!.x} cy={p!.y} r={3.5} stroke="#d49a2a" fill="#fff"/>)}{neck!==null&&right!==null?<line x1={left} x2={right} y1={neck} y2={neck} stroke="#d49a2a" strokeDasharray="5 4"/>:null}{target!==null&&right!==null&&['CONFIRMED','RETESTED'].includes(pattern.state)?<line data-pattern-target x1={left} x2={right} y1={target} y2={target} stroke="#697f98" strokeDasharray="2 5"/>:null}</g>;
 }
 const controlsHeader=<>
<div className={styles.heading}><strong>{mobile?'结构 · 箱体 · 通道':'结构 · 箱体 · 画线'}</strong>{mobile?<div className={styles.channelControls} role="group" aria-label="趋势通道方向">{([['all','全部通道'],['up','上行'],['down','下行']] as const).map(([value,label])=><button type="button" key={value} aria-pressed={channelDirection===value} onClick={()=>setChannelDirection(value)}>{label}</button>)}<button type="button" aria-label="绘制平行通道" disabled={!coordinates||!bars.length||!ready||Boolean(study.error)} onClick={()=>selectTool('channel')}>手绘通道</button></div>:null}<button type="button" aria-pressed={automatic} onClick={toggleAutomatic}>{automatic?'自动标记已开':'自动标记已关'}</button></div>
  <details className={styles.tools} open={compact||undefined}><summary>绘图工具与图层 <span>{drawings.length} 个本机图形</span></summary><div className={styles.toolBody}>
   <div className={styles.toolRow}><WorkspaceSelect label="画线工具" value={tool} disabled={!coordinates||!bars.length||!ready||Boolean(study.error)} onValueChange={v=>selectTool(v as DrawingKind|'cursor')} options={[{value:'cursor',label:'浏览 / 选择'},...Object.entries(drawingLabels).map(([value,label])=>({value,label}))]}/><label><input type="checkbox" checked={snap} onChange={e=>setSnap(e.target.checked)}/>吸附 OHLC</label><button type="button" disabled={!draft.length&&tool==='cursor'} onClick={()=>selectTool('cursor')}>取消画线</button></div>
   <div className={styles.toolRow}>{Object.entries(groupNames).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key as keyof typeof layers]} onChange={e=>setLayers(v=>({...v,[key]:e.target.checked}))}/>{label}</label>)}<label><input type="checkbox" checked={inactive} onChange={e=>setInactive(e.target.checked)}/>显示失效区域</label><label><input type="checkbox" checked={profile} disabled={analysis.volumeProfile.kind==='UNAVAILABLE'} onChange={e=>setProfile(e.target.checked)}/>估算成交量分布</label></div>
   <div className={styles.toolRow}><button type="button" disabled={!ready||!history.past.length} onClick={()=>{setHistory(h=>({...h,past:h.past.slice(0,-1),present:h.past.at(-1)!,future:[h.present,...h.future]}));setSelection('');}}>撤销</button><button type="button" disabled={!ready||!history.future.length} onClick={()=>{setHistory(h=>({...h,past:[...h.past,h.present],present:h.future[0],future:h.future.slice(1)}));setSelection('');}}>重做</button><button type="button" disabled={!ready} onClick={download}>导出绘图</button><button type="button" disabled={!ready} onClick={()=>importInput.current?.click()}>导入绘图</button><input ref={importInput} hidden type="file" accept="application/json,.json" aria-label="导入绘图文件" onChange={async e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;if(file.size>100000){setNotice('文件过大，仅接受 100 KB 以内的绘图 JSON。');return;}const request=++importRevision.current,requestedScope=scope;try{const text=await file.text();if(currentScope.current!==requestedScope||request!==importRevision.current)return;load(text);}catch{if(currentScope.current===requestedScope&&request===importRevision.current)setNotice('文件读取失败，原绘图已保留。');}}}/></div>
   <p>每类默认显示最新区域，可在下方查看较早区域。自动结构只在闭合柱确认后出现；区域被突破或回补后会标记状态。时间显示 UTC，交易时段按当地时区处理夏令时，仅完整区间生成。手动画线仅本机保存，不改变账户或止损订单。</p>
  </div></details>
 </>;
 const controlsDetails=<>
<div className={styles.legend}><span>{({UP:'上行结构',DOWN:'下行结构',RANGE:'区间结构',UNAVAILABLE:'结构预热中'} as const)[analysis.regime]}</span><span>{visible.length} 个自动标记 · {manual.length}/{drawings.length} 个本机图形</span></div>
  {mobile?<details className={styles.details} data-testid="chart-pattern-analysis"><summary>形态与条件推演 · {patterns[0]?(patterns[0].kind==='DOUBLE_TOP'?'双顶 M':'双底 W'):'未确认形态'}</summary><div className={styles.detailBody}><label className={styles.patternToggle}><input type="checkbox" checked={showPatterns} onChange={event=>setShowPatterns(event.target.checked)}/>标记顶底、颈线与测算目标</label>{patterns.map(pattern=><article key={pattern.id}><strong>{pattern.kind==='DOUBLE_TOP'?'双顶 M':'双底 W'} · {chartPatternStates[pattern.state]}</strong><p>形态可见于 {utc(pattern.formedAt)}。{analysis.regime==='DOWN'?'当前为下行结构。':analysis.regime==='UP'?'当前为上行结构。':'当前没有单向趋势确认。'}</p><dl className={styles.patternLevels}><div><dt>颈线</dt><dd>{fmt(pattern.neckline)}</dd></div><div><dt>顶部 / 底部失效线</dt><dd>{fmt(pattern.invalidation)}</dd></div><div><dt>高度测算目标</dt><dd>{fmt(pattern.target)}</dd></div></dl><p>{pattern.direction==='down'?`闭合价低于 ${fmt(pattern.neckline-pattern.buffer)} 才确认破位；之后另柱回访颈线并收在其下方，才标记回抽。收回 ${fmt(pattern.neckline+pattern.buffer)} 或收盘越过失效线则失败。`:`闭合价高于 ${fmt(pattern.neckline+pattern.buffer)} 才确认突破；之后另柱回访颈线并收在其上方，才标记回踩。跌回 ${fmt(pattern.neckline-pattern.buffer)} 或收盘越过失效线则失败。`}</p><p>目标由两顶 / 两底均值至颈线的高度等距投射，不含成本，不代表成交、时间预测或盈利概率。同柱触及目标与失效线时保留顺序不明。</p></article>)}{!patterns.length?<p>当前连续闭合窗口没有符合规则的双顶 / 双底，不强行套图。可切换产品或周期。</p>:null}<p>规则：左右各两柱确认拐点；两顶 / 底相差不超过形态高度 10%，深度超过颈线 0.3%，间隔 4–80 柱。这里只做条件分析，不改变模拟策略和订单。</p><a href="https://www.tradingview.com/support/solutions/43000653211-chart-pattern-double-top/" target="_blank" rel="noreferrer">形态定义参考 ↗</a></div></details>:null}
  {mobile&&automatic&&layers.structure&&!visible.some(a=>a.kind==='channel')?<p className={styles.caution}>当前闭合窗口没有符合所选方向的有效通道。可切换周期、显示失效区域，或点击“手绘通道”用三个锚点绘制。</p>:null}
  {visible.length?<div className={styles.regionList} aria-label="当前结构区域">{visible.map(a=><button key={a.id} type="button" aria-pressed={selection===a.id} onClick={()=>setSelection(a.id)}><span style={{background:color(a)}}/>{shortLabel(a)}</button>)}</div>:null}
  {selectedAuto?<p className={styles.selectedRegion} role="status"><strong style={{color:color(selectedAuto)}}>{selectedAuto.label}</strong><span>{states[selectedAuto.state]} · {fmt(selectedAuto.low)}—{fmt(selectedAuto.high)}</span><button type="button" aria-label="取消区域选择" onClick={()=>setSelection('')}>取消选择</button></p>:null}
  {profile&&analysis.volumeProfile.kind==='OHLCV_ESTIMATE'?<p className={styles.profileNote}>POC / VAH / VAL 为当前闭合柱 OHLCV 分箱估算。</p>:null}
  {analysis.warnings.length?<p className={styles.caution}>{analysis.warnings.join('；')}</p>:null}
  <details className={styles.details}><summary>区域依据与绘图管理</summary><div className={styles.detailBody}>
   <WorkspaceSelect label="查看图形依据" value={selection} placeholder="选择自动区域或手动画线" onValueChange={setSelection} options={[...candidates.slice(-150).map(a=>({value:a.id,label:`${a.label} · ${states[a.state]}`})),...drawings.map(d=>({value:d.id,label:`手动 · ${d.label}${d.locked?' · 锁定':''}${manual.includes(d)?'':' · 当前窗口外'}`}))]}/>
   {selectedAuto?<article><strong>{selectedAuto.label} · {states[selectedAuto.state]}</strong><p>{selectedAuto.explanation}</p><p>规则：{selectedAuto.rule}</p><p>价格 {fmt(selectedAuto.low)}—{fmt(selectedAuto.high)} · 触碰 {selectedAuto.touches} 次</p><p>确认于 {utc(selectedAuto.confirmedAt)}</p></article>:null}
   {selectedManual?<article><div className={styles.toolRow}><strong>{selectedManual.label}</strong><button type="button" onClick={()=>commit(drawings.map(d=>d.id===selection?{...d,locked:!d.locked}:d))}>{selectedManual.locked?'解锁图形':'锁定图形'}</button><button type="button" disabled={selectedManual.locked} onClick={()=>{commit(drawings.filter(d=>d.id!==selection));setSelection('');}}>删除选中图形</button></div><label className={styles.rename}>图形名称<input aria-label="图形名称" key={selectedManual.id+selectedManual.label} defaultValue={selectedManual.label} maxLength={80} disabled={selectedManual.locked} onBlur={e=>{const label=e.target.value.trim();if(label&&label!==selectedManual.label)commit(drawings.map(d=>d.id===selection?{...d,label}:d));}}/></label>{selectedManual.points.map((p,i)=><div className={styles.anchor} key={selectedManual.id+':'+i+':'+p.time+':'+p.price}><label>锚点 {i+1} · UTC<input aria-label={`锚点 ${i+1} 时间`} type="datetime-local" step="1" defaultValue={new Date(p.time).toISOString().slice(0,19)} disabled={selectedManual.locked} onBlur={e=>{if(e.target.value!==new Date(p.time).toISOString().slice(0,19))changePoint(i,'time',e.target.value+'Z');}}/></label><label>价格<input aria-label={`锚点 ${i+1} 价格`} type="number" step="any" min="0" defaultValue={p.price} disabled={selectedManual.locked} onBlur={e=>{if(Number(e.target.value)!==p.price)changePoint(i,'price',e.target.value);}}/></label></div>)}{editError?<p role="alert" className={styles.caution}>{editError}</p>:null}</article>:null}
   {!visible.length&&!drawings.length?<p>当前没有已确认区域。可积累更多闭合柱，或使用手动画线。</p>:null}
   <p>{analysis.volumeProfile.reason} 成交量分布为当前加载窗口的 OHLCV 分箱估算，不是逐笔买卖量、订单流或完整市场成交分布。</p>
  </div></details>
  {references.length?<p className={styles.caution}>研究计划与账本参考线：{references.length} 条（主图最多绘制 24 条，超出价格视窗的线不会拉伸图表）。计划线不提交订单，账本线为最近手动读取的快照。</p>:null}
  <details className={styles.details}><summary>分档计划 · 成本回测 · 模拟持仓</summary><ChartScenarioPanel bars={study.error?[]:bars} context={context} analysis={analysis} onPlanLevels={receivePlan} onAccountLevels={receiveAccounts} showLedger={!historical}/></details>
  {!historical?<ChartDerivativesContext context={context}/>:null}
 </>;
 return <section className={styles.workbench} data-testid="chart-workbench" data-phone-compact={compact||undefined} data-instrument={context.instrumentId} data-source={context.source} data-timeframe={context.timeframe}>
  {!compact?controlsHeader:null}
  {external?<p className={styles.notice} role="status">另一标签页更新了绘图。<button type="button" onClick={()=>load(external)}>载入最新绘图</button><button type="button" onClick={()=>{setExternal(null);setNotice('继续使用本页绘图，并保存为最新版本。');}}>保留本页绘图</button></p>:null}
  {notice?<p className={styles.notice} role="status">{notice}</p>:null}
  {study.error?<p className={styles.caution} role="alert">{study.error} 未生成分析区域或研究交易。</p>:null}
  <div className={styles.surface} ref={surface} data-chart-surface>
   {coordinates&&bars.length&&tool==='cursor'?<ChartGesturePlane coordinates={coordinates}/>:null}
   {children}
   {coordinates&&bars.length>0&&!study.error&&size.width>0?<svg data-testid="chart-drawing-overlay" className={`${styles.overlay} ${tool!=='cursor'?styles.drawing:''}`} width={size.width} height={size.height} viewBox={`0 0 ${size.width} ${size.height}`} onPointerUp={point} tabIndex={tool==='cursor'?-1:0} aria-label={tool==='cursor'?'价格结构与绘图图层':'点选主图锚点，Escape 取消'} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();selectTool('cursor');e.stopPropagation();}}}>
    {visible.map(drawAutomatic)}{automatic&&showPatterns?patterns.map(drawPattern):null}
    {profile&&analysis.volumeProfile.kind==='OHLCV_ESTIMATE'?<g data-testid="volume-profile-estimate">{analysis.volumeProfile.bins.map((bin,i)=>{const y1=coordinates.priceToY(bin.high),y2=coordinates.priceToY(bin.low),max=Math.max(...analysis.volumeProfile.bins.map(b=>b.volume));if(y1===null||y2===null||!max)return null;const width=Math.min(65,size.width*.18)*bin.volume/max;return <rect key={i} x={size.width-width} y={Math.min(y1,y2)} width={width} height={Math.max(1,Math.abs(y1-y2))} fill="#9c83d543"/>;})}{[['POC',analysis.volumeProfile.poc],['VAH',analysis.volumeProfile.vah],['VAL',analysis.volumeProfile.val]].map(([label,value])=>{const y=typeof value==='number'?coordinates.priceToY(value):null;return y===null?null:<line key={label as string} x1={0} x2={size.width} y1={y} y2={y} stroke="#8b62be" strokeDasharray="3 3"/>;})}</g>:null}
    {manual.map(drawManual)}{references.slice(0,24).map(line=>{const y=coordinates.priceToY(line.price);return y===null||y<0||y>size.height?null:<g key={line.id} data-reference-id={line.id}><title>{line.label} · {fmt(line.price)}</title><line x1={0} x2={size.width} y1={y} y2={y} stroke={line.color} strokeWidth={1.3} strokeDasharray="6 4"/></g>;})}
    {placedLabels.map(label=><g key={label.id} data-chart-label={label.id} className={styles.label} onClick={label.selectionId?e=>{if(tool==='cursor'){e.stopPropagation();setSelection(label.selectionId!);}}:undefined}><title>{label.title}</title><line className={styles.labelLeader} x1={Math.max(0,Math.min(size.width,label.x))} y1={label.y} x2={Math.max(label.left+6,Math.min(label.left+label.width-6,label.x))} y2={label.top+label.height/2} stroke={label.color}/><rect x={label.left} y={label.top} width={label.width} height={label.height} rx={6} fill="#ffffffed" stroke={label.color} strokeOpacity={label.selectionId===selection?1:.28}/><text x={label.left+8} y={label.top+15} fill={label.color}>{label.displayText}</text></g>)}
    {draft.map((p,i)=>{const q=xy(p);return q?<circle key={i} cx={q.x} cy={q.y} r={5} fill="#f09325"/>:null;})}
   </svg>:null}
  </div>
  {!compact?controlsDetails:null}
  {compact&&mobileTools?.open?<AnalysisDialog title="图层与绘图" onClose={mobileTools.close}>{controlsHeader}{controlsDetails}</AnalysisDialog>:null}
 </section>;
}
