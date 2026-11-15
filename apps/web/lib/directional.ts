import {z} from 'zod';

const DecimalString=z.string().regex(/^-?\d+(\.\d+)?([Ee][+-]?\d+)?$/);
const Time=z.iso.datetime({offset:true});
export const DirectionalQuote=z.object({instrument:z.string(),source:z.string(),at:Time,bid:DecimalString,ask:DecimalString});
export const DirectionalConfig=z.object({instrument:z.string(),quote_source:z.string(),initial_cash:DecimalString,tick_size:DecimalString,quantity_step:DecimalString,max_quantity:DecimalString,max_order_quote:DecimalString,fee_rate:DecimalString,slippage:DecimalString,news_pre_seconds:z.number().int(),news_post_seconds:z.number().int(),news_sources:z.array(z.string()).optional()});
export const PriceActionRules=['STRUCTURE_FVG','RANGE_REVERSION','TREND_PULLBACK'] as const;
export const PriceActionTimeframes=['1m','5m','15m','1h','4h','1d'] as const;
export const DirectionalPriceAnalysis=z.object({rule_version:z.string(),state:z.enum(['WARMUP','READY','UNAVAILABLE']),samples:z.number().int().nonnegative(),required:z.number().int().nonnegative(),target:z.enum(['LONG','SHORT']).nullable(),available_at:Time.nullable(),reason:z.string(),evidence:z.record(z.string(),z.unknown()),decision_status:z.string().optional(),evaluated_at:Time.optional(),source:z.string().optional(),bar_sha256:z.string().optional(),resynchronized_at:Time.optional(),resume_after:Time.optional()});
const LegacyQuoteAnalysis=z.object({state:z.enum(['WARMUP','READY']),samples:z.number().int().nonnegative(),required:z.number().int().nonnegative(),target:z.enum(['LONG','SHORT','FLAT']).nullable(),sma:DecimalString.nullable(),available_at:Time.optional(),sample_type:z.string().optional()});
export const DirectionalAccount=z.object({
 account_id:z.uuid(),revision:z.number().int().nonnegative(),status:z.enum(['RUNNING','STOPPED']),created_at:Time,updated_at:Time,
 mode:z.literal('RESEARCH_PAPER'),trading_enabled:z.literal(false),config:DirectionalConfig,
 metadata:z.object({strategy:z.enum(['MANUAL','SMA20_QUOTES','AI_RESEARCH','PRICE_ACTION']),quantity:DecimalString.nullable().optional(),stop_loss_percent:DecimalString.nullable().optional(),take_profit_percent:DecimalString.nullable().optional(),rule:z.enum(PriceActionRules).optional(),timeframe:z.enum(PriceActionTimeframes).optional()}),
 balance:DecimalString,equity:DecimalString,fees:DecimalString,gross_pnl:DecimalString,unrealized_pnl:DecimalString,entry_halted:z.boolean(),
 position:z.object({direction:z.enum(['LONG','SHORT']),quantity:DecimalString,entry:DecimalString,stop_loss:DecimalString,take_profit:DecimalString}).nullable(),
 pending:z.object({request_id:z.string(),target:z.enum(['LONG','SHORT','FLAT']),accepted_at:Time}).nullable(),
 fills:z.array(z.object({action:z.enum(['OPEN','CLOSE']),direction:z.enum(['LONG','SHORT']),quantity:DecimalString,price:DecimalString,fee:DecimalString,reason:z.string(),quote_at:Time,simulated:z.literal(true)})),
 fills_count:z.number().int().nonnegative(),fills_truncated:z.boolean(),events:z.number().int().nonnegative(),journal_sha256:z.string().nullable(),last_quote:DirectionalQuote.nullable(),
 entry_gate:z.string(),recent_quotes:z.array(DirectionalQuote),
 last_outcome:z.object({kind:z.enum(['DECISION','ENTRY_REJECTED','ENTRY_STOPPED','FILL']),status:z.enum(['ACCEPTED','REJECTED','STOPPED','FILLED']),reason:z.string(),request_id:z.string().nullable(),at:Time}).nullable(),
 analysis:z.union([DirectionalPriceAnalysis,LegacyQuoteAnalysis]).nullable().optional(),
});
export type DirectionalView=z.infer<typeof DirectionalAccount>;
export const PublicNewsStatus=z.object({source:z.string(),state:z.enum(['READY','UNAVAILABLE','STALE','WARMUP']),observed_at:Time.nullable(),coverage_start:Time.nullable(),coverage_end:Time.nullable(),error:z.string().nullable(),scope:z.string(),feeds:z.array(z.object({name:z.string(),url:z.url(),checked_at:Time.nullable(),upstream_date:z.string().nullable(),last_modified:z.string().nullable(),sha256:z.string().nullable(),events:z.number().int().nonnegative()})),next_high:z.array(z.object({event_id:z.string(),title:z.string(),scheduled_at:Time,url:z.url(),instruments:z.array(z.string())}))});
export const DirectionalStatus=z.object({service:z.string(),trading_enabled:z.literal(false),mode:z.literal('RESEARCH_PAPER'),news:z.object({configured:z.boolean(),state:z.string(),source:z.string().nullable().optional(),monitor:PublicNewsStatus.nullable().optional()}),ai:z.object({configured:z.literal(false)}),feed:z.record(z.string(),z.object({state:z.string(),last_checked:Time.nullable(),last_quote:DirectionalQuote.nullable()})),instruments:z.array(z.object({instrument:z.string(),quote_source:z.string(),currency:z.string(),unit:z.string()}))});
export const DirectionalAccounts=z.object({accounts:z.array(DirectionalAccount),trading_enabled:z.literal(false)});

const exactDecimal=z.string().trim().max(128).regex(/^-?(?:\d+(?:\.\d+)?|\.\d+)(?:[Ee][+-]?\d+)?$/).refine(value=>Number.isFinite(Number(value)),'请输入有限数字');
const positiveDecimal=(max:number)=>exactDecimal.refine(value=>Number(value)>0&&Number(value)<=max,`数值须大于 0 且不超过 ${max}`);
const nonnegativeDecimal=(max:number)=>exactDecimal.refine(value=>Number(value)>=0&&Number(value)<=max,`数值须在 0 至 ${max} 之间`);
const createFields=z.object({instrument:z.enum(['XAU/USD','BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT']),strategy:z.enum(['MANUAL','SMA20_QUOTES','AI_RESEARCH','PRICE_ACTION']),initial_cash:positiveDecimal(1000000),tick_size:positiveDecimal(1),quantity_step:positiveDecimal(1),max_quantity:positiveDecimal(1000),max_order_quote:positiveDecimal(1000000),fee_rate:nonnegativeDecimal(.1),slippage:nonnegativeDecimal(1000),news_pre_seconds:z.string().regex(/^\d+$/).refine(value=>Number(value)<=86400,'消息窗口最大 86400 秒'),news_post_seconds:z.string().regex(/^\d+$/).refine(value=>Number(value)<=86400,'消息窗口最大 86400 秒'),quantity:z.string(),stop_loss_percent:z.string(),take_profit_percent:z.string(),rule:z.string(),timeframe:z.string()});
export type DirectionalCreateFields=z.infer<typeof createFields>;
function quantityMultiple(quantity:string,step:string):boolean {
 const parts=(value:string)=>{const [base,power='0']=value.toLowerCase().split('e'),[whole,fraction='']=base.split('.');return {digits:BigInt((whole||'0')+fraction),scale:fraction.length-Number(power)};};
 const a=parts(quantity),b=parts(step),scale=Math.max(a.scale,b.scale);
 return a.digits*10n**BigInt(scale-a.scale)%(b.digits*10n**BigInt(scale-b.scale))===0n;
}
/** Build only approved settings; no candles, news or client observation clocks. */
export function directionalCreateBody(fields:Record<string,string>,requestId:string){
 const values=createFields.parse(fields),automatic=values.strategy==='SMA20_QUOTES'||values.strategy==='PRICE_ACTION';
 if(Number(values.max_order_quote)>Number(values.initial_cash))throw new Error('单笔金额上限不能超过初始资金。');
 if(values.instrument==='XAU/USD'&&Number(values.max_quantity)>100)throw new Error('黄金最大数量不能超过 100 盎司。');
 if(automatic){
  positiveDecimal(1000).parse(values.quantity);
  exactDecimal.refine(value=>Number(value)>=.0001&&Number(value)<=.5,'止损比例须在 0.0001 至 0.5 之间').parse(values.stop_loss_percent);
  exactDecimal.refine(value=>Number(value)>=.0001&&Number(value)<1,'止盈比例须不小于 0.0001 且小于 1').parse(values.take_profit_percent);
  if(Number(values.quantity)>Number(values.max_quantity)||!quantityMultiple(values.quantity,values.quantity_step))throw new Error('自动数量须在最大数量内，且是数量步长的整数倍。');
 }
 const {quantity,stop_loss_percent,take_profit_percent,rule,timeframe,...base}=values;
 const priceAction=values.strategy==='PRICE_ACTION';
 if(priceAction){z.enum(PriceActionRules).parse(rule);z.enum(PriceActionTimeframes).parse(timeframe);if(values.instrument==='XAU/USD'&&timeframe!=='1m')throw new Error('黄金只支持真实闭合报价采样的 1m 周期。');}
 return {...base,request_id:z.uuid().parse(requestId),news_pre_seconds:Number(values.news_pre_seconds),news_post_seconds:Number(values.news_post_seconds),...(priceAction?{rule,timeframe}:{}),...(values.strategy==='AI_RESEARCH'?{}:{quantity:automatic?quantity:null,stop_loss_percent:automatic?stop_loss_percent:null,take_profit_percent:automatic?take_profit_percent:null})};
}
export function directionalStrategyName(value:string){return ({MANUAL:'手动结构化决策',SMA20_QUOTES:'SMA20 报价基线',AI_RESEARCH:'AI 研究',PRICE_ACTION:'闭合 K 线自动规则'} as Record<string,string>)[value]??value;}
export function directionalRuleName(value:string){return ({STRUCTURE_FVG:'结构转变与 FVG',RANGE_REVERSION:'箱体边界反弹',TREND_PULLBACK:'趋势回撤'} as Record<string,string>)[value]??value;}

const Reasons:Record<string,string>={QUOTE_UNAVAILABLE:'尚无有效报价，禁止开仓',QUOTE_STALE:'报价延迟，禁止开仓',SERVER_CONFIGURED:'新闻已配置，按每账户校验有效性',CLEAR:'允许新风险',NEWS_UNAVAILABLE:'新闻源未接入，禁止开仓',NEWS_STALE:'新闻已过期，禁止开仓',NEWS_FROM_FUTURE:'新闻时间异常，禁止开仓',NEWS_SOURCE_UNTRUSTED:'新闻来源未获准',NEWS_COVERAGE_INCOMPLETE:'新闻覆盖不足，禁止开仓',HIGH_IMPACT_EVENT:'高影响事件窗口，禁止开仓',ENTRY_HALTED:'已停止新风险',ACCEPTED:'已接收，等待后续报价',REJECTED:'已拒绝',FLATTEN_BEFORE_REENTRY:'先平仓后才能更换方向',STOP_LOSS:'止损退出',TAKE_PROFIT:'止盈退出',SIGNAL_EXIT:'信号平仓',SIGNAL_ENTRY:'信号入场',DECISION_EXPIRED:'决策过期，未成交',BRACKET_ALREADY_TRIGGERED:'保护价已被报价触发，未开仓',BRACKET_INVALID_AT_FILL:'成交时保护价无效',COLLATERAL_OR_ORDER_LIMIT:'资金或金额上限拒绝',SIZE_OR_PRICE_LIMIT:'数量或价格精度拒绝'};
const Outcomes:Record<string,string>={FILLED:'已模拟成交',STOPPED:'已停止新风险',USER_STOP:'手动停止新风险',OPERATION_LIMIT:'达到记账操作预算',PROTECTIVE_EXITS_ONLY:'仅保留保护退出'};
const RuleReasons:Record<string,string>={CLOSED_HISTORY_WARMUP:'有效闭合历史不足，等待预热',GAP_RESET_WARMUP:'历史存在缺口，重新积累闭合样本',NO_NEW_SIGNAL:'当前闭合柱没有新的规则入场信号',NEW_LONG_SIGNAL:'本柱产生新的多头规则候选',NEW_SHORT_SIGNAL:'本柱产生新的空头规则候选',HISTORY_UNAVAILABLE:'闭合历史服务不可用，禁止新开仓',HISTORY_WARMUP:'闭合历史预热中，禁止新开仓',CLOSED_BAR_STALE:'等待下一根新鲜闭合柱，不追补旧信号',SAME_DIRECTION:'已有同向持仓，保持保护价',QUEUED:'已接收，等待下一条真实报价',RULE_CHECKPOINT_INVALID:'规则检查点异常，仅保留持仓保护',BAR_ALREADY_EVALUATED:'此闭合柱已处理，不重复下单',HIGH_IMPACT_EVENT_CROSSED:'旧开仓意图跨过高影响窗口，已撤销'};
const RecoveryReasons:Record<string,string>={HISTORY_REVISED:'闭合历史发生修订，旧开仓意图已撤销',HISTORY_RESYNC_PENDING:'历史修订隔离中，等待独立快照确认',HISTORY_RESYNC_WAIT_NEW_BAR:'历史已同步，等待下一根新闭合柱',HISTORY_RESTART_RECHECK:'服务重启已撤销旧开仓意图，等待新信号',RULE_HISTORY_STATE_UNAVAILABLE:'规则状态写入异常，已停止新风险，保护退出继续'};
export function directionalReason(value:string){return value==='NO_SIGNAL'?'本柱未排队新决策':Reasons[value]??Outcomes[value]??RuleReasons[value]??RecoveryReasons[value]??value;}
export class DirectionalError extends Error{readonly status:number;constructor(message:string,status:number){super(message);this.status=status;this.name='DirectionalError';}}
export async function directionalRequest<T>(path:string,schema:z.ZodType<T>,body?:unknown,signal?:AbortSignal):Promise<T>{
 const response=await fetch('/api/directional/'+path,{method:body===undefined?'GET':'POST',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000)});
 const value:unknown=await response.json();
 if(!response.ok)throw new DirectionalError(typeof value==='object'&&value!==null&&'detail'in value&&typeof value.detail==='string'?value.detail:`模拟请求失败（${response.status}），请刷新核对。`,response.status);
 return schema.parse(value);
}
