import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';

// Synthetic contract fixtures only; never imported by the app or presented as market evidence.
process.env.NEXT_PUBLIC_MARKET_PROVIDER='bitget';
const require=createRequire(import.meta.url);
async function bundle(entry,plugins=[]){const result=await build({entryPoints:[new URL(entry,import.meta.url).pathname],bundle:true,platform:'node',format:'cjs',write:false,plugins});const module={exports:{}};new Function('module','exports','require',result.outputFiles[0].text)(module,module.exports,require);return module.exports;}
const {ReplayState,defaultReplayForm,replayRequest,formFromReplay,changedReplayForm,assertReplayCommand}=await bundle('./replay-model.ts');
const stamp=i=>new Date(Date.UTC(2024,0,1,i)).toISOString();
const row=i=>({open_time:stamp(i),available_at:stamp(i+1),regime:{rule_version:'er-atr-v1',label:'UNAVAILABLE',volatility:'UNAVAILABLE',efficiency_ratio:null,atr_fraction:null,available_at:stamp(i+1)},values:Object.fromEntries(['sma','ema','bollinger_upper','bollinger_lower','vwap','rsi','atr','macd','macd_signal','macd_histogram'].map(key=>[key,null])),confirmed_swings:[],structure_breaks:[]});
const state=(cursor=2,revision=cursor)=>({session_id:'11111111-1111-4111-8111-111111111111',revision,cursor,total:8,status:cursor===8?'ENDED':'READY',clock:stamp(cursor),trading_enabled:false,manifest:{version:'closed-bar-replay-v2',strategy:null,strategy_version:null,parameters:null,decision_rule:'target-on-close-v1',symbol:'BTCUSDT',timeframe:'1h',period:2,snapshot_sha256:'a'.repeat(64),as_of:stamp(8),start:stamp(0),end:stamp(8)},candles:Array.from({length:cursor},(_,i)=>({instrument_id:'bitget:spot:BTC-USDT',timeframe:'1h',open_time:stamp(i),close_time:stamp(i+1),open:String(100+i),high:String(102+i),low:String(99+i),close:String(101+i),volume:'10',is_closed:true,source:'bitget.public'})),indicators:Array.from({length:cursor},(_,i)=>row(i)),decisions:[]});

test('replay draft validates integer bounds, timezone, future cutoff and SMA ordering before a request',()=>{
 const form={...defaultReplayForm};assert.equal(replayRequest(form).limit,120);
 for(const limit of ['', '1','1001','2.5','NaN'])assert.throws(()=>replayRequest({...form,limit}));
 for(const period of ['','1','501','Infinity'])assert.throws(()=>replayRequest({...form,period}));
 assert.throws(()=>replayRequest({...form,cutoff:'2024-01-01T00:00:00'}),/时区/);
 assert.throws(()=>replayRequest({...form,cutoff:'2099-01-01T00:00:00Z'}),/当前时间/);
 assert.throws(()=>replayRequest({...form,strategy:'sma_long_flat_v1',fast:'20',slow:'10'}),/慢周期/);
 assert.deepEqual(replayRequest({...form,strategy:'sma_long_flat_v1',fast:'10',slow:'20'}).parameters,{fast:10,slow:20});
});
test('acknowledged prefix accepts zero and final states, rejects future/misaligned/foreign data and trading',()=>{
 for(const cursor of [0,2,8])assert.equal(ReplayState.parse(state(cursor)).cursor,cursor);
 for(const mutate of [s=>s.cursor=3,s=>s.status='ENDED',s=>s.clock=stamp(1),s=>s.candles[1].is_closed=false,s=>s.candles[1].instrument_id='bitget:spot:ETH-USDT',s=>s.candles[1].timeframe='15m',s=>s.candles[1].open_time=stamp(0),s=>s.indicators[1].available_at=stamp(3),s=>s.indicators[1].regime.available_at=stamp(3),s=>s.trading_enabled=true,s=>s.manifest.snapshot_sha256='invalid',s=>s.indicators[1].values.ema=Infinity]){const invalid=state();mutate(invalid);assert.equal(ReplayState.safeParse(invalid).success,false);}
 const future=state();future.indicators[1].structure_breaks=[{direction:'up',kind:'BOS',level:'101',available_at:stamp(3)}];assert.equal(ReplayState.safeParse(future).success,false);
 const equivalent=state();equivalent.indicators[0].open_time='2024-01-01T08:00:00+08:00';assert.equal(ReplayState.safeParse(equivalent).success,true);
});
test('editing config is separate from immutable active snapshot and equivalent parameter order is not dirty',()=>{
 const view=ReplayState.parse(state());const form=formFromReplay(view);assert.equal(changedReplayForm(form,view),false);
 for(const patch of [{symbol:'ETHUSDT'},{timeframe:'15m'},{period:'3'},{limit:'9'},{cutoff:stamp(7)}])assert.equal(changedReplayForm({...form,...patch},view),true);
 assert.equal(view.manifest.symbol,'BTCUSDT');
 const sma=state();sma.manifest.strategy='sma_long_flat_v1';sma.manifest.parameters={slow:20,fast:10};assert.equal(changedReplayForm(formFromReplay(sma),sma),false);
});
test('step/reset acknowledgements require exact session, snapshot, revision and cursor',()=>{
 const before=ReplayState.parse(state());assert.doesNotThrow(()=>assertReplayCommand(before,ReplayState.parse(state(3)),'step'));assert.doesNotThrow(()=>assertReplayCommand(before,ReplayState.parse(state(0,3)),'reset'));
 for(const patch of [{cursor:4},{revision:2},{session_id:'22222222-2222-4222-8222-222222222222'},{manifest:{...before.manifest,snapshot_sha256:'b'.repeat(64)}}])assert.throws(()=>assertReplayCommand(before,{...state(3),...patch},'step'),/不一致/);
});

test('optional canvas theme leaves default palette and history contract unchanged',async()=>{
 const records=[];
 globalThis.__REPLAY_CHART_MOCK__={createChart:(element,options)=>{const record={options,series:[],range:null,history:[]};records.push(record);return {addSeries:(type,options)=>{const item={type,options,data:[]};record.series.push(item);return {applyOptions:next=>Object.assign(item.options,next),setData:data=>{item.data=data;},update:data=>{item.data.push(data);}};},priceScale:()=>({applyOptions(){}}),panes:()=>[],removeSeries(){},remove(){},timeScale:()=>({getVisibleRange:()=>null,setVisibleRange(){},fitContent(){},setVisibleLogicalRange(){},subscribeVisibleLogicalRangeChange(){}})};},createSeriesMarkers:()=>({setMarkers(){}}),CandlestickSeries:'candles',HistogramSeries:'histogram',LineSeries:'line',ColorType:{Solid:'solid'},CrosshairMode:{Normal:0}};
 const adapter=await bundle('../../lib/chart-adapter.ts',[{name:'test-canvas',setup(plugin){plugin.onResolve({filter:/^lightweight-charts$/},()=>({path:'chart',namespace:'synthetic'}));plugin.onLoad({filter:/.*/,namespace:'synthetic'},()=>({contents:'module.exports=globalThis.__REPLAY_CHART_MOCK__;',loader:'js'}));}}]);
 const original=adapter.createChartAdapter({}),approved=adapter.createChartAdapter({},{theme:'approved',pricePrecision:()=>5}),bars=state().candles;
 original.setHistory(bars);approved.setHistory(bars);original.setIndicators(state().indicators,true);approved.setIndicators(state().indicators,true);
 assert.equal(records[0].options.layout.textColor,'#6e6e73');assert.equal(records[0].series[0].options.upColor,'#00875a');assert.equal(records[0].series[0].options.priceFormat,undefined);assert.equal(records[1].options.layout.textColor,'#8296b3');assert.equal(records[1].options.layout.fontSize,11);assert.equal(records[1].series[0].options.upColor,'#08bc76');assert.equal(records[1].series[0].options.downColor,'#f14c68');assert.equal(records[1].series[0].options.priceFormat.precision,5);
 for(let i=0;i<records[0].series.length;i++)assert.deepEqual(records[0].series[i].data,records[1].series[i].data);
 original.dispose();approved.dispose();delete globalThis.__REPLAY_CHART_MOCK__;
});
