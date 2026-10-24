'use client';
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {z} from 'zod';
import {Symbols,Intervals} from '../../lib/market';
import {createChartAdapter,type ChartAdapter} from '../../lib/chart-adapter';
import WorkspaceSelect from '../../components/workspace-select';
import {displayPrecision,formatPrice} from '../analysis/analysis-format';
import {ReplayState,defaultReplayForm,replayRequest,formFromReplay,changedReplayForm,assertReplayCommand,type ReplayView,type ReplayForm} from './replay-model';
import './replay.css';

const KEY='fc666.replay.session.v1',SPEED_KEY='fc666.replay.speed.v1';
const strategies={none:'只回放行情',ema_long_flat_v1:'EMA Long / Flat v1',sma_long_flat_v1:'SMA Long / Flat v1'};
const labels={UNAVAILABLE:'预热中',RANGE:'震荡',TREND_UP:'上行趋势',TREND_DOWN:'下行趋势'};
type IconName='play'|'pause'|'step'|'reset'|'settings'|'down'|'download'|'chart'|'clock'|'info'|'arrow';
function Icon({name}:{name:IconName}){const paths:Record<IconName,string>={play:'m9 5 11 7-11 7Z',pause:'M8 5v14M16 5v14',step:'m5 5 10 7-10 7ZM19 5v14',reset:'M4 10a8 8 0 1 1 1 8M4 4v6h6',settings:'M4 7h16M4 17h16M9 4v6M15 14v6',down:'m6 9 6 6 6-6',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',chart:'M3 4v16h18M6 14l5-6 4 4 6-8',clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 6v6l4 2',info:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 11v6M12 7h.01',arrow:'M5 12h14m-6-6 6 6-6 6'};return <svg className="rp-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]}/></svg>;}
const utc=(value:string)=>new Date(value).toISOString().replace('T',' ').replace(/\.000Z$/,' UTC');
const message=(error:unknown,fallback:string)=>error instanceof z.ZodError?'回放响应校验失败，已保留最后确认的进度。':error instanceof Error?error.message:fallback;

export default function ReplayWorkspace(){
 const [form,setForm]=useState<ReplayForm>({...defaultReplayForm}),[ready,setReady]=useState(false),[configOpen,setConfigOpen]=useState(false);
 const [view,setView]=useState<ReplayView|null>(null),[busy,setBusy]=useState(false),[restoring,setRestoring]=useState(false),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(1),[indicators,setIndicators]=useState(false),[follow,setFollow]=useState(true),[error,setError]=useState(''),[storageWarning,setStorageWarning]=useState(false);
 const audit=useRef<Record<string,unknown>|null>(null),current=useRef<ReplayView|null>(null),locked=useRef(false),playRequested=useRef(false),element=useRef<HTMLDivElement>(null),chart=useRef<ChartAdapter|null>(null),configElement=useRef<HTMLElement>(null);
 function storage(action:'get'|'set'|'remove',key:string,value=''){try{if(action==='get')return localStorage.getItem(key);if(action==='set')localStorage.setItem(key,value);else localStorage.removeItem(key);}catch{setStorageWarning(true);}return null;}
 function pause(){playRequested.current=false;setPlaying(false);}
 function adopt(next:ReplayView){audit.current=next;current.current=next;setView(next);storage('set',KEY,next.session_id);if(next.status==='ENDED')pause();}
 async function read(id:string,signal?:AbortSignal){const response=await fetch(`/api/replay/sessions/${id}`,{cache:'no-store',signal:signal??AbortSignal.timeout(15000)});if(response.status===404){storage('remove',KEY);throw new Error('保存的回放会话已不存在，请创建新回放。');}if(!response.ok)throw new Error('回放状态暂不可用，请稍后重试。');const data=await response.json();const next=ReplayState.parse(data);if(next.session_id!==id)throw new Error('回放会话不匹配。');return next;}
 useEffect(()=>{if(element.current){chart.current=createChartAdapter(element.current,{theme:'approved',pricePrecision:()=>{const active=current.current;return active?displayPrecision(active.manifest.symbol,undefined,active.candles.at(-1)?.close)??undefined:undefined;}});return()=>{chart.current?.dispose();chart.current=null;};}},[]);
 useEffect(()=>{chart.current?.setHistory(view?.candles??[]);chart.current?.setIndicators(view?.indicators??[],indicators);if(follow&&view?.cursor)chart.current?.fit();},[view,indicators,follow]);
 useEffect(()=>{
  setReady(true);const saved=storage('get',KEY),savedSpeed=Number(storage('get',SPEED_KEY));if([.5,1,2,4].includes(savedSpeed))setSpeed(savedSpeed);
  if(!saved||!z.uuid().safeParse(saved).success)return;
  const abort=new AbortController();locked.current=true;setBusy(true);setRestoring(true);
  void read(saved,abort.signal).then(next=>{if(!abort.signal.aborted){adopt(next);setForm(formFromReplay(next));}}).catch(e=>{if(!abort.signal.aborted)setError(message(e,'恢复失败。'));}).finally(()=>{if(!abort.signal.aborted){locked.current=false;setBusy(false);setRestoring(false);}});
  return()=>abort.abort();
 // Restore one acknowledged session; playback always starts paused.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[]);
 function change<K extends keyof ReplayForm>(key:K,value:ReplayForm[K]){pause();setForm(previous=>({...previous,[key]:value}));setError('');}
 function openConfiguration(){pause();setConfigOpen(value=>!value);}
 async function create(event:FormEvent){
  event.preventDefault();if(!ready||locked.current)return;pause();setError('');
  let request:ReturnType<typeof replayRequest>;try{request=replayRequest(form);}catch(e){setError(message(e,'请检查回放配置。'));return;}
  locked.current=true;setBusy(true);
  try{const response=await fetch('/api/replay/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request),signal:AbortSignal.timeout(15000)});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'回放参数或历史数据不可用。');const next=ReplayState.parse(data);adopt(next);setForm(formFromReplay(next));setConfigOpen(false);}
  catch(e){setError(message(e,'创建失败，原回放进度已保留。'));}finally{locked.current=false;setBusy(false);}
 }
 async function command(action:'step'|'reset',automatic=false){
  const before=current.current;if(!before||locked.current||(automatic&&!playRequested.current))return;locked.current=true;setBusy(true);setError('');
  try{const response=await fetch(`/api/replay/sessions/${before.session_id}/command`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:before.revision,action,count:1}),signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(response.status===409?'回放版本冲突，已暂停并同步进度。':'推进未确认，已暂停并尝试同步进度。');const data=await response.json();const next=ReplayState.parse(data);assertReplayCommand(before,next,action);adopt(next);}
  catch(e){pause();setError(message(e,'回放连接失败。'));try{const next=await read(before.session_id);if(next.manifest.snapshot_sha256!==before.manifest.snapshot_sha256)throw new Error('回放快照不一致。');adopt(next);}catch{/* Keep the acknowledged prefix; never repeat an unconfirmed mutation. */}}
  finally{locked.current=false;setBusy(false);}
 }
 useEffect(()=>{
  if(!playing||!view)return;let active=true;let timer:ReturnType<typeof setTimeout>;
  async function tick(){if(!active||!playRequested.current)return;if(locked.current){timer=setTimeout(tick,50);return;}await command('step',true);if(active&&playRequested.current&&current.current?.status!=='ENDED')timer=setTimeout(tick,1000/speed);}
  timer=setTimeout(tick,1000/speed);return()=>{active=false;clearTimeout(timer);};
 // Commands use the latest acknowledged state through current, never a captured cursor.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[playing,speed,view?.session_id]);
 function download(){if(!view||!audit.current||busy||playing)return;const url=URL.createObjectURL(new Blob([JSON.stringify(audit.current,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`fc666-replay-prefix-${view.session_id}-${view.cursor}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),0);}
 const last=view?.candles.at(-1),latest=view?.indicators.at(-1),dirty=view?changedReplayForm(form,view):false,progress=view?view.cursor/view.total*100:0;
 const price=(raw:string|null|undefined)=>formatPrice(raw==null?null:Number(raw),view?.manifest.symbol??form.symbol,undefined,last?.close??raw??undefined);
 const stateLabel=restoring?'恢复进度中':view?.status==='ENDED'?'回放已结束':playing?'正在播放':busy?'正在确认当前步':view?'已暂停':'尚未创建';
 return <main className="replay-workspace replay-page" aria-busy={!ready||restoring}>
  <div className="rp-page-heading"><div><h1>历史市场回放</h1><p>逐根查看已到达的行情、指标与策略目标</p></div><span className="rp-pill"><Icon name="clock"/>固定历史快照</span></div>
  <div className="rp-layout">
   <aside ref={configElement} className={`rp-card rp-config${configOpen?' is-open':''}`} aria-label="回放配置">
    <button className="rp-mobile-config" disabled={!ready||busy} aria-label="回放配置" aria-expanded={configOpen} onClick={openConfiguration}><span><Icon name="settings"/><strong>回放配置</strong><small>{form.symbol.replace('USDT','/USDT')} · {form.timeframe}</small></span><Icon name="down"/></button>
    <div className="rp-config-heading"><h2>回放配置</h2><span className="rp-caption">独立于当前快照</span></div>
    <form onSubmit={create} noValidate>
     <fieldset disabled={!ready||busy}><div className="rp-field"><span>交易对</span><WorkspaceSelect label="回放交易对" value={form.symbol} disabled={!ready||busy} onValueChange={value=>change('symbol',value)} options={Symbols.map(value=>({value,label:value.replace('USDT',' / USDT')}))}/></div>
     <div className="rp-field-pair"><div className="rp-field"><span>周期</span><WorkspaceSelect label="回放周期" value={form.timeframe} disabled={!ready||busy} onValueChange={value=>change('timeframe',value)} options={Intervals.map(value=>({value,label:value}))}/></div><label className="rp-field"><span>历史柱数</span><input aria-label="回放柱数" required type="number" min="2" max="1000" step="1" value={form.limit} onChange={e=>change('limit',e.target.value)}/></label></div>
     <div className="rp-field"><span>策略</span><WorkspaceSelect label="回放策略" value={form.strategy} disabled={!ready||busy} onValueChange={value=>change('strategy',value)} options={Object.entries(strategies).map(([value,label])=>({value,label}))}/></div>
     <label className="rp-field"><span>指标 / EMA 周期</span><input aria-label="回放均线周期" required type="number" min="2" max="500" step="1" value={form.period} onChange={e=>change('period',e.target.value)}/></label>
     {form.strategy==='sma_long_flat_v1'?<div className="rp-field-pair"><label className="rp-field"><span>SMA 快周期</span><input aria-label="回放 SMA 快周期" required type="number" min="2" max="499" step="1" value={form.fast} onChange={e=>change('fast',e.target.value)}/></label><label className="rp-field"><span>SMA 慢周期</span><input aria-label="回放 SMA 慢周期" required type="number" min="3" max="500" step="1" value={form.slow} onChange={e=>change('slow',e.target.value)}/></label></div>:null}
     <label className="rp-field"><span>截止时间 <small>可留空 · 需含时区</small></span><input aria-label="回放截止时间（含时区，可空）" value={form.cutoff} onChange={e=>change('cutoff',e.target.value)} placeholder="例如 2026-10-01T00:00:00Z" autoComplete="off"/></label></fieldset>
     <div className="rp-config-note"><Icon name="info"/><p>新建后从零根开始。修改配置会暂停；当前固定快照在新建成功前始终保留。</p></div>
     <button className="rp-button primary full" disabled={!ready||busy||playing} type="submit">{busy&&!restoring?'正在确认…':'创建固定回放'}<Icon name="arrow"/></button>
     {dirty?<p className="rp-dirty">配置已修改，尚未创建新快照</p>:null}
    </form>
   </aside>
   <div className="rp-main">
    {error?<p className="rp-error" role="alert"><Icon name="info"/><span>{error}</span></p>:null}
    {storageWarning?<p className="rp-notice">浏览器未允许保存偏好；刷新后可能无法恢复会话。</p>:null}
    <section className="rp-card rp-player" data-testid="replay-state" data-cursor={view?.cursor??''} data-revision={view?.revision??''} data-playing={playing} data-status={view?.status??'EMPTY'}>
     <div className="rp-player-heading"><div><span className="rp-kicker">闭合柱回放 <span className="rp-pill">只读历史</span></span><h2>{(view?.manifest.symbol??form.symbol).replace('USDT',' / USDT')}<small>{view?.manifest.timeframe??form.timeframe}</small></h2></div><span className={`rp-play-status${playing?' active':''}`}><i/>{stateLabel}</span></div>
     <div className="rp-quote"><strong data-testid="replay-close" data-value={last?.close??''}>{last?price(last.close):'尚未推进'}</strong><span>USDT</span><div><small>EMA {view?.manifest.period??form.period}</small><b>{latest?.values.ema==null?'预热中':price(String(latest.values.ema))}</b></div></div>
     <div className="rp-chart-head"><div><Icon name="chart"/><span>已到达 K 线</span></div><div className="rp-segmented" role="group" aria-label="回放图表设置"><button disabled={!ready} className={follow?'active':''} aria-pressed={follow} onClick={()=>setFollow(value=>!value)}>跟随回放</button><button disabled={!ready} className={indicators?'active':''} aria-pressed={indicators} onClick={()=>setIndicators(value=>!value)}>回放指标</button></div></div>
     <div className={`rp-chart-wrap${indicators?' has-indicators':''}`}><div ref={element} className="rp-canvas" data-testid="replay-chart" aria-label="已到达历史K线图"/>{!last?<div className="rp-chart-empty" role="status"><Icon name={restoring?'clock':'chart'}/><strong>{restoring?'正在恢复已确认的进度':view?'准备好，等待第一根闭合柱':'从一段历史开始'}</strong><p>{view?'点击播放或推进一根，逐步查看已到达行情。':'配置产品与周期后，创建固定回放。'}</p>{!view&&!restoring?<button className="rp-button soft rp-setup-shortcut" disabled={!ready} onClick={()=>{setConfigOpen(true);configElement.current?.scrollIntoView({block:'start',behavior:'smooth'});}}>配置回放<Icon name="settings"/></button>:null}</div>:null}</div>
     <div className="rp-progress"><div><span>{view?`${view.cursor} / ${view.total} 根`:'等待固定快照'}</span><strong>{view?`${progress.toFixed(1)}%`:'—'}</strong></div><progress aria-label="回放进度" max={view?.total??1} value={view?.cursor??0}/></div>
     <div className="rp-transport"><div className="rp-transport-buttons"><button className="rp-icon-button" aria-label="从头重置" title="从头重置" disabled={!ready||!view||busy||view.cursor===0} onClick={()=>{pause();void command('reset');}}><Icon name="reset"/></button><button className="rp-button primary rp-play" disabled={!ready||!view||(!playing&&(busy||view.status==='ENDED'))} onClick={()=>{if(playing)pause();else{playRequested.current=true;setPlaying(true);}}}><Icon name={playing?'pause':'play'}/>{playing?'暂停回放':'播放回放'}</button><button className="rp-icon-button" aria-label="推进一根" title="推进一根" disabled={!ready||!view||busy||playing||view.status==='ENDED'} onClick={()=>void command('step')}><Icon name="step"/></button></div><div className="rp-speed"><span>速度</span><WorkspaceSelect label="回放速度" value={speed} disabled={!ready} className="rp-speed-select" onValueChange={raw=>{const value=Number(raw);if(![.5,1,2,4].includes(value))return;setSpeed(value);storage('set',SPEED_KEY,String(value));}} options={[.5,1,2,4].map(value=>({value:String(value),label:`${value}×`}))}/></div></div>
     <div className="rp-clock"><Icon name="clock"/><span>回放时钟</span><time data-testid="replay-clock">{view?utc(view.clock):'—'}</time><span className="rp-regime">{latest?labels[latest.regime.label]:'预热中'}</span></div>
    </section>
    <section className="rp-card rp-decisions" data-testid="replay-decisions" data-count={view?.decisions.length??0}><div className="rp-panel-head"><div><h2>策略目标</h2><p>{view?.manifest.strategy?strategies[view.manifest.strategy]:'当前仅回放行情'} · 决策目标不代表订单或成交</p></div><button className="rp-button small" aria-label="下载已确认前缀" title="下载已确认前缀" disabled={!ready||!view||busy||playing} onClick={download}><Icon name="download"/><span>下载已确认前缀</span></button></div>
     {view?.decisions.length?<><p className="rp-caption">共 {view.decisions.length} 条，显示最近 100 条</p><div className="rp-table-wrap"><table><thead><tr><th>可用时间 UTC</th><th>目标</th><th>柱开始 UTC</th><th>事件 ID</th></tr></thead><tbody>{view.decisions.slice(-100).map(event=><tr key={event.event_id}><td>{utc(event.available_at)}</td><td><span className={`rp-target ${event.target==='LONG'?'long':'flat'}`}>{event.target==='LONG'?'做多目标':'空仓目标'}</span></td><td>{utc(event.bar_open_time)}</td><td title={event.event_id}>{event.event_id.slice(0,12)}</td></tr>)}</tbody></table></div><div className="rp-mobile-decisions">{view.decisions.slice(-100).map(event=><article key={event.event_id}><div><span className={`rp-target ${event.target==='LONG'?'long':'flat'}`}>{event.target==='LONG'?'做多目标':'空仓目标'}</span><time>{utc(event.available_at)}</time></div><p>柱开始 {utc(event.bar_open_time)}</p><small>事件 {event.event_id.slice(0,12)}</small></article>)}</div></>:<div className="rp-decision-empty"><Icon name="chart"/><span>{!view?'创建快照后查看策略决策':!view.manifest.strategy?'选择 EMA / SMA 策略并创建新快照，才能查看目标决策。':'预热期间没有决策；推进至指标成熟后显示。'}</span></div>}
    </section>
    <details className="rp-card rp-details"><summary><span>数据与回放口径</span><Icon name="down"/></summary><div><p>固定历史快照仅返回已到达的闭合价格与因果指标，不订阅实时行情。速度为每秒目标根数，实际速度取决于服务器确认。</p>{view?<dl><div><dt>固定截止 UTC</dt><dd>{utc(view.manifest.as_of)}</dd></div><div><dt>历史范围 UTC</dt><dd>{utc(view.manifest.start)} 至 {utc(view.manifest.end)}</dd></div><div><dt>参数</dt><dd>EMA / 指标周期 {view.manifest.period}；{JSON.stringify(view.manifest.parameters??{})}</dd></div><div><dt>快照 SHA256</dt><dd>{view.manifest.snapshot_sha256}</dd></div></dl>:null}<p>刷新默认暂停并恢复已确认进度。冲突或网络异常会暂停并读取服务器状态，不自动重发推进请求；暂停时已发出的当前步仍可能完成。修改配置不会改变当前会话，需要新建才能采用。</p><p>指标仅使用当前前缀：SMA / EMA / Bollinger / VWAP、RSI / MACD / ATR 与已确认结构。预热不足留空，重置后不保留未来指标或决策。</p><p>不含逐笔成交、历史订单簿、柱内路径、模拟下单或实盘下单。图表技术：<a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a>。</p></div></details>
   </div>
  </div>
 </main>;
}
