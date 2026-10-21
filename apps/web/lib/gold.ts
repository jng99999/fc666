import {z} from 'zod';
const decimal=z.string().refine(v=>v.trim()!==''&&Number.isFinite(Number(v)));
export const GoldAccount=z.object({id:z.uuid(),revision:z.number().int().positive(),status:z.enum(['ACTIVE','PAUSED']),mode:z.enum(['MANUAL','SMA20']),cash:decimal,quantity:decimal,basis:decimal,fees:decimal,realized:decimal,
 currency:z.literal('USD'),instrument:z.literal('XAU/USD'),trading_enabled:z.literal(false),phase:z.string(),created_at:z.number(),updated_at:z.number(),last_bar:z.number().nullable(),
 config:z.object({initial_cash:decimal,allocation:decimal,max_spread:decimal,commission:decimal,slippage:decimal}),intent:z.unknown().nullable()});
export const GoldEvent=z.object({seq:z.number().int().positive(),at:z.number(),hash:z.string(),kind:z.string(),details:z.record(z.string(),z.unknown())});
export const GoldDetail=GoldAccount.extend({events:z.array(GoldEvent)});
export const GoldMarket=z.object({trading_enabled:z.literal(false),fresh:z.boolean(),checked_at:z.number(),source:z.literal('swissquote.public:AT:AT:standard:XAU/USD'),
 quote:z.object({ts:z.number(),bid:decimal,ask:decimal,mid:decimal,spread:decimal,symbol:z.literal('XAU/USD'),currency:z.literal('USD'),unit:z.literal('troy_ounce')}).nullable(),
 bars:z.array(z.object({open_time:z.number(),close_time:z.number(),open:decimal,high:decimal,low:decimal,close:decimal,samples:z.number()})),
 analysis:z.object({status:z.enum(['WARMUP','STALE','READY']),bars:z.number(),required:z.number(),sma20:decimal.nullable(),target:z.enum(['LONG','FLAT']).optional(),available_at:z.number().optional(),range_high:decimal.optional(),range_low:decimal.optional()}),feed:z.object({state:z.string(),last_checked:z.number().nullable()})});
export type GoldAccountType=z.infer<typeof GoldAccount>;
export type GoldDetailType=z.infer<typeof GoldDetail>;
export type GoldEventType=z.infer<typeof GoldEvent>;
export type GoldMarketType=z.infer<typeof GoldMarket>;
export async function goldRequest(path:string,signal?:AbortSignal,body?:unknown){
 const response=await fetch(`/api/gold/${path}`,{method:body===undefined?'GET':'POST',headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:signal??AbortSignal.timeout(15000),cache:'no-store'});
 const data:unknown=await response.json();
 if(!response.ok){const detail=z.object({detail:z.string()}).safeParse(data);const issues=z.object({detail:z.array(z.object({loc:z.array(z.union([z.string(),z.number()])),msg:z.string()}))}).safeParse(data);throw new Error(detail.success?detail.data.detail:issues.success?issues.data.detail.slice(0,3).map(v=>`${v.loc.filter(p=>p!=='body').join('.')}：${v.msg}`).join('；'):'黄金请求校验失败，请检查参数并刷新账本。');}
 return data;
}
