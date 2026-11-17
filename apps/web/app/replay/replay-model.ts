import {z} from 'zod';
import {CandleSchema,IndicatorSchema,Symbols,Intervals} from '../../lib/market';

const Decision=z.object({event_id:z.string().regex(/^[0-9a-f]{64}$/),schema_version:z.literal(1),type:z.literal('TARGET_DECISION'),rule:z.literal('target-on-close-v1'),strategy:z.enum(['ema_long_flat_v1','sma_long_flat_v1']),strategy_version:z.literal(1),parameters:z.record(z.string(),z.unknown()),bar_open_time:z.iso.datetime({offset:true}),available_at:z.iso.datetime({offset:true}),target:z.enum(['LONG','FLAT']),execution:z.literal('NOT_IMPLEMENTED')});
export const ReplayState=z.object({session_id:z.uuid(),revision:z.number().int().nonnegative(),cursor:z.number().int().nonnegative(),total:z.number().int().min(2).max(1000),status:z.enum(['READY','ENDED']),clock:z.iso.datetime({offset:true}),trading_enabled:z.literal(false),manifest:z.object({version:z.enum(['closed-bar-replay-v1','closed-bar-replay-v2']),strategy:z.enum(['ema_long_flat_v1','sma_long_flat_v1']).nullable().optional(),strategy_version:z.number().int().nullable().optional(),parameters:z.record(z.string(),z.unknown()).nullable().optional(),decision_rule:z.literal('target-on-close-v1').optional(),symbol:z.enum(Symbols),timeframe:z.enum(Intervals),period:z.number().int().min(2).max(500),snapshot_sha256:z.string().regex(/^[0-9a-f]{64}$/),as_of:z.iso.datetime({offset:true}),start:z.iso.datetime({offset:true}),end:z.iso.datetime({offset:true})}),candles:z.array(CandleSchema),indicators:IndicatorSchema.shape.rows,decisions:z.array(Decision).default([])}).superRefine((state,context)=>{
 const fail=(message:string)=>context.addIssue({code:'custom',message});
 if(state.cursor>state.total||state.candles.length!==state.cursor||state.indicators.length!==state.cursor||(state.status==='ENDED')!==(state.cursor===state.total))fail('回放前缀或进度不一致');
 const clock=Date.parse(state.clock),instrument=state.candles[0]?.instrument_id;
 if(clock!==Date.parse(state.candles.at(-1)?.close_time??state.manifest.start))fail('回放时钟与前缀不一致');
 if(state.candles.some((bar,index)=>!bar.is_closed||bar.timeframe!==state.manifest.timeframe||bar.instrument_id!==instrument||!bar.instrument_id.endsWith(`:${state.manifest.symbol.slice(0,-4)}-USDT`)||Date.parse(bar.close_time)>clock||(index>0&&Date.parse(bar.open_time)!==Date.parse(state.candles[index-1].close_time))))fail('已拒绝未来或不连续行情');
 if(state.indicators.some((row,index)=>Date.parse(row.open_time)!==Date.parse(state.candles[index]?.open_time)||Date.parse(row.available_at)!==Date.parse(state.candles[index]?.close_time)||Date.parse(row.regime.available_at)>clock||row.confirmed_swings.some(swing=>Date.parse(swing.confirmed_at)>clock)||row.structure_breaks.some(event=>Date.parse(event.available_at)>clock)))fail('已拒绝未来或错位指标');
 const reached=new Map(state.candles.map(bar=>[Date.parse(bar.open_time),Date.parse(bar.close_time)]));
 if(state.decisions.some(event=>Date.parse(event.available_at)>clock||reached.get(Date.parse(event.bar_open_time))!==Date.parse(event.available_at)))fail('已拒绝未来策略决策');
});
export type ReplayView=z.infer<typeof ReplayState>;
export const defaultReplayForm={strategy:'none',fast:'10',slow:'20',symbol:'BTCUSDT',timeframe:'1h',limit:'120',period:'20',cutoff:''};
export type ReplayForm=typeof defaultReplayForm;
export function replayRequest(form:ReplayForm,now=Date.now()){
 const integer=(raw:string,min:number,max:number,label:string)=>{if(!/^\d+$/.test(raw)||Number(raw)<min||Number(raw)>max)throw new Error(`${label}需为 ${min}–${max} 的整数。`);return Number(raw);};
 const symbol=z.enum(Symbols).parse(form.symbol),timeframe=z.enum(Intervals).parse(form.timeframe);
 const limit=integer(form.limit,2,1000,'回放柱数'),period=integer(form.period,2,500,'均线周期');
 const strategy=z.enum(['none','ema_long_flat_v1','sma_long_flat_v1']).parse(form.strategy);
 const as_of=form.cutoff.trim()||null;
 if(as_of&&(!z.iso.datetime({offset:true}).safeParse(as_of).success||Date.parse(as_of)>now))throw new Error('截止时间需包含时区，且不能晚于当前时间。');
 const parameters=strategy==='none'?null:strategy==='ema_long_flat_v1'?{period}:{fast:integer(form.fast,2,499,'SMA 快周期'),slow:integer(form.slow,3,500,'SMA 慢周期')};
 if(parameters&&typeof parameters.fast==='number'&&typeof parameters.slow==='number'&&parameters.fast>=parameters.slow)throw new Error('SMA 慢周期需大于快周期。');
 return {symbol,timeframe,limit,period,as_of,strategy:strategy==='none'?null:strategy,parameters};
}
export function formFromReplay(view:ReplayView):ReplayForm{return {strategy:view.manifest.strategy??'none',fast:String(view.manifest.parameters?.fast??10),slow:String(view.manifest.parameters?.slow??20),symbol:view.manifest.symbol,timeframe:view.manifest.timeframe,limit:String(view.total),period:String(view.manifest.period),cutoff:view.manifest.as_of};}
export function changedReplayForm(form:ReplayForm,view:ReplayView){
 try{const request=replayRequest(form),parameters=request.parameters??{},active=view.manifest.parameters??{};return request.symbol!==view.manifest.symbol||request.timeframe!==view.manifest.timeframe||request.limit!==view.total||request.period!==view.manifest.period||request.strategy!==(view.manifest.strategy??null)||(request.as_of!==null&&Date.parse(request.as_of)!==Date.parse(view.manifest.as_of))||Object.keys(parameters).length!==Object.keys(active).length||Object.entries(parameters).some(([key,value])=>active[key]!==value);}catch{return true;}
}
export function assertReplayCommand(before:ReplayView,next:ReplayView,action:'step'|'reset'){
 const cursor=action==='reset'?0:Math.min(before.cursor+1,before.total),revision=before.revision+(cursor===before.cursor?0:1);
 if(next.session_id!==before.session_id||next.manifest.snapshot_sha256!==before.manifest.snapshot_sha256||next.cursor!==cursor||next.revision!==revision)throw new Error('回放确认与请求不一致，已暂停并同步。');
}
