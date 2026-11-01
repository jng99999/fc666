import {z} from 'zod';

const DecimalString=z.string().regex(/^-?\d+(\.\d+)?([Ee][+-]?\d+)?$/);
const Time=z.iso.datetime({offset:true});
export const DirectionalQuote=z.object({instrument:z.string(),source:z.string(),at:Time,bid:DecimalString,ask:DecimalString});
export const DirectionalConfig=z.object({instrument:z.string(),quote_source:z.string(),initial_cash:DecimalString,tick_size:DecimalString,quantity_step:DecimalString,max_quantity:DecimalString,max_order_quote:DecimalString,fee_rate:DecimalString,slippage:DecimalString,news_pre_seconds:z.number().int(),news_post_seconds:z.number().int()});
export const DirectionalAccount=z.object({
 account_id:z.uuid(),revision:z.number().int().nonnegative(),status:z.enum(['RUNNING','STOPPED']),created_at:Time,updated_at:Time,
 mode:z.literal('RESEARCH_PAPER'),trading_enabled:z.literal(false),config:DirectionalConfig,
 metadata:z.object({strategy:z.enum(['MANUAL','SMA20_QUOTES','AI_RESEARCH']),quantity:DecimalString.nullable().optional(),stop_loss_percent:DecimalString.nullable().optional(),take_profit_percent:DecimalString.nullable().optional()}),
 balance:DecimalString,equity:DecimalString,fees:DecimalString,gross_pnl:DecimalString,unrealized_pnl:DecimalString,entry_halted:z.boolean(),
 position:z.object({direction:z.enum(['LONG','SHORT']),quantity:DecimalString,entry:DecimalString,stop_loss:DecimalString,take_profit:DecimalString}).nullable(),
 pending:z.object({request_id:z.string(),target:z.enum(['LONG','SHORT','FLAT']),accepted_at:Time}).nullable(),
 fills:z.array(z.object({action:z.enum(['OPEN','CLOSE']),direction:z.enum(['LONG','SHORT']),quantity:DecimalString,price:DecimalString,fee:DecimalString,reason:z.string(),quote_at:Time,simulated:z.literal(true)})),
 fills_count:z.number().int().nonnegative(),fills_truncated:z.boolean(),events:z.number().int().nonnegative(),journal_sha256:z.string().nullable(),last_quote:DirectionalQuote.nullable(),
 entry_gate:z.string(),recent_quotes:z.array(DirectionalQuote),
 last_outcome:z.object({kind:z.enum(['DECISION','ENTRY_REJECTED','ENTRY_STOPPED','FILL']),status:z.enum(['ACCEPTED','REJECTED','STOPPED','FILLED']),reason:z.string(),request_id:z.string().nullable(),at:Time}).nullable(),
});
export type DirectionalView=z.infer<typeof DirectionalAccount>;
export const DirectionalStatus=z.object({service:z.string(),trading_enabled:z.literal(false),mode:z.literal('RESEARCH_PAPER'),news:z.object({configured:z.boolean(),state:z.string()}),ai:z.object({configured:z.literal(false)}),feed:z.record(z.string(),z.object({state:z.string(),last_checked:Time.nullable(),last_quote:DirectionalQuote.nullable()})),instruments:z.array(z.object({instrument:z.string(),quote_source:z.string(),currency:z.string(),unit:z.string()}))});
export const DirectionalAccounts=z.object({accounts:z.array(DirectionalAccount),trading_enabled:z.literal(false)});

const Reasons:Record<string,string>={QUOTE_UNAVAILABLE:'尚无有效报价，禁止开仓',QUOTE_STALE:'报价延迟，禁止开仓',SERVER_CONFIGURED:'新闻已配置，按每账户校验有效性',CLEAR:'允许新风险',NEWS_UNAVAILABLE:'新闻源未接入，禁止开仓',NEWS_STALE:'新闻已过期，禁止开仓',NEWS_FROM_FUTURE:'新闻时间异常，禁止开仓',NEWS_SOURCE_UNTRUSTED:'新闻来源未获准',NEWS_COVERAGE_INCOMPLETE:'新闻覆盖不足，禁止开仓',HIGH_IMPACT_EVENT:'高影响事件窗口，禁止开仓',ENTRY_HALTED:'已停止新风险',ACCEPTED:'已接收，等待后续报价',REJECTED:'已拒绝',FLATTEN_BEFORE_REENTRY:'先平仓后才能更换方向',STOP_LOSS:'止损退出',TAKE_PROFIT:'止盈退出',SIGNAL_EXIT:'信号平仓',SIGNAL_ENTRY:'信号入场',DECISION_EXPIRED:'决策过期，未成交',BRACKET_ALREADY_TRIGGERED:'保护价已被报价触发，未开仓',BRACKET_INVALID_AT_FILL:'成交时保护价无效',COLLATERAL_OR_ORDER_LIMIT:'资金或金额上限拒绝',SIZE_OR_PRICE_LIMIT:'数量或价格精度拒绝'};
const Outcomes:Record<string,string>={FILLED:'已模拟成交',STOPPED:'已停止新风险',USER_STOP:'手动停止新风险',OPERATION_LIMIT:'达到记账操作预算',PROTECTIVE_EXITS_ONLY:'仅保留保护退出'};
export function directionalReason(value:string){return Reasons[value]??Outcomes[value]??value;}
export class DirectionalError extends Error{constructor(message:string,readonly status:number){super(message);this.name='DirectionalError';}}
export async function directionalRequest<T>(path:string,schema:z.ZodType<T>,body?:unknown,signal?:AbortSignal):Promise<T>{
 const response=await fetch('/api/directional/'+path,{method:body===undefined?'GET':'POST',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),cache:'no-store',signal:signal?AbortSignal.any([signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000)});
 const value:unknown=await response.json();
 if(!response.ok)throw new DirectionalError(typeof value==='object'&&value!==null&&'detail'in value&&typeof value.detail==='string'?value.detail:`模拟请求失败（${response.status}），请刷新核对。`,response.status);
 return schema.parse(value);
}
