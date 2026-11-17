import {z} from 'zod';
import {GoldEvent} from './gold';

const decimal=z.string().regex(/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/).refine(v=>Number.isFinite(Number(v)));
const money=z.object({initial_cash:decimal,allocation:decimal,max_spread:decimal,commission:decimal,slippage:decimal});
const policy=z.object({model_id:z.string(),actor_sha256:z.string().regex(/^[a-f0-9]{64}$/),context_hash:z.string().regex(/^[a-f0-9]{64}$/),protocol:z.string(),feature_version:z.string()});
export const PolicyAccount=z.object({id:z.uuid(),revision:z.number().int().positive(),status:z.enum(['ACTIVE','PAUSED','STOPPED']),mode:z.literal('POLICY'),cash:decimal,quantity:decimal,basis:decimal,fees:decimal,realized:decimal,currency:z.literal('USD'),instrument:z.literal('XAU/USD'),trading_enabled:z.literal(false),phase:z.string(),created_at:z.number(),updated_at:z.number(),last_bar:z.number().nullable(),config:money.extend({mode:z.literal('POLICY'),policy})});
export const PolicyDetail=PolicyAccount.extend({events:z.array(GoldEvent),next_before:z.number().int().positive().nullable()});
export const PolicyList=z.object({accounts:z.array(PolicyAccount),enabled:z.boolean(),trading_enabled:z.literal(false)});
export const PolicyStatus=z.object({enabled:z.boolean(),trading_enabled:z.literal(false),source:z.literal('swissquote.public:AT:AT:standard:XAU/USD'),required_bars:z.literal(100),complete_bars:z.number().int().min(0).max(100),latest_close_at:z.number().nullable(),warmup_status:z.enum(['WAITING_HISTORY','WAITING_FRESH_CLOSED_HOUR','READY']),status:z.enum(['DISABLED','NO_QUALIFIED_MODELS','WAITING_HISTORY','WAITING_FRESH_CLOSED_HOUR','READY']),models:z.array(z.object({id:z.string().regex(/^[a-zA-Z0-9_-]{1,64}$/),protocol:z.string(),feature_version:z.string(),valid_until:z.number().int(),account_config:money})),checked_at:z.number()}).superRefine((value,ctx)=>{
 if(value.enabled===(value.status==='DISABLED'))ctx.addIssue({code:'custom',message:'执行开关与状态不一致'});
 if(value.status==='READY'&&(!value.models.length||value.complete_bars!==100||value.warmup_status!=='READY'||value.latest_close_at===null))ctx.addIssue({code:'custom',message:'模拟就绪状态缺少模型或完整小时历史'});
 if(value.status==='NO_QUALIFIED_MODELS'&&value.models.length)ctx.addIssue({code:'custom',message:'模型列表与准入状态不一致'});
});
export const PolicyCommand=z.object({request_id:z.uuid(),expected_revision:z.number().int().positive(),action:z.enum(['DECIDE','PAUSE','RESUME','STOP'])});
export const PolicyCreate=money.extend({request_id:z.uuid(),mode:z.literal('POLICY'),model_id:z.string().regex(/^[a-zA-Z0-9_-]{1,64}$/),experimental:z.literal(true)});
export const PendingPolicy=z.discriminatedUnion('kind',[
 z.object({kind:z.literal('create'),body:PolicyCreate,uncertain:z.boolean().default(true)}),
 z.object({kind:z.literal('command'),account_id:z.uuid(),body:PolicyCommand,uncertain:z.boolean().default(true)}),
]);
const hash=z.string().regex(/^[a-f0-9]{64}$/);
export const PolicyRequestReceipt=z.discriminatedUnion('found',[
 z.object({found:z.literal(false),request_id:z.uuid(),absence_proves_not_applied:z.literal(false)}),
 z.object({found:z.literal(true),request_id:z.uuid(),account_id:z.uuid(),seq:z.number().int().positive(),kind:z.enum(['CREATED','POLICY_DECIDE','POLICY_PAUSE','POLICY_RESUME','POLICY_STOP']),event_hash:hash,request_hash:hash,hash_scope:z.enum(['ACCOUNT_CONFIG','COMMAND_BODY']),state:PolicyAccount,details:z.record(z.string(),z.unknown()),account:PolicyAccount}),
]);
export const PolicyFill=z.object({side:z.enum(['BUY','SELL']),ounces:decimal,price:decimal,fee:decimal});

function decimalIdentity(value:string){
 const [mantissa,exponent='0']=value.toLowerCase().split('e');
 const [whole,fraction='']=mantissa.replace('-','').split('.');
 const digits=(whole+fraction).replace(/^0+/,'');
 if(!digits)return '0';
 const significant=digits.replace(/0+$/,'');
 return `${mantissa.startsWith('-')?'-':''}${significant}e${BigInt(exponent)-BigInt(fraction.length)+BigInt(digits.length-significant.length)}`;
}
function sortedJson(value:unknown):string{
 if(Array.isArray(value))return `[${value.map(sortedJson).join(',')}]`;
 if(value!==null&&typeof value==='object')return `{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([key,item])=>`${JSON.stringify(key)}:${sortedJson(item)}`).join(',')}}`;
 return JSON.stringify(value);
}
async function ledgerHash(value:unknown){
 const canonical=sortedJson(value).replace(/[\u007f-\uffff]/g,char=>`\\u${char.charCodeAt(0).toString(16).padStart(4,'0')}`);
 return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical))),byte=>byte.toString(16).padStart(2,'0')).join('');
}
export async function verifyPolicyReceipt(request:PendingPolicyType,value:unknown){
 const receipt=PolicyRequestReceipt.parse(value);
 if(receipt.request_id!==request.body.request_id)throw new Error('回执请求编号不一致，保留原请求待核对。');
 if(!receipt.found)return receipt;
 if(receipt.account_id!==receipt.state.id||receipt.account_id!==receipt.account.id||receipt.seq!==receipt.state.revision||receipt.account.revision<receipt.seq)throw new Error('回执账户或版本不一致，保留原请求待核对。');
 if(request.kind==='create'){
  if(receipt.kind!=='CREATED'||receipt.seq!==1||receipt.hash_scope!=='ACCOUNT_CONFIG'||receipt.request_hash!==await ledgerHash(receipt.state.config)||receipt.state.config.policy.model_id!==request.body.model_id||Object.keys(money.shape).some(key=>decimalIdentity(receipt.state.config[key as keyof typeof money.shape])!==decimalIdentity(request.body[key as keyof typeof money.shape])))throw new Error('创建回执与原账户配置不一致，保留原请求待核对。');
 }else{
  const digest=await ledgerHash(request.body);
  if(receipt.account_id!==request.account_id||receipt.kind!==`POLICY_${request.body.action}`||receipt.hash_scope!=='COMMAND_BODY'||receipt.seq!==request.body.expected_revision+1||receipt.request_hash!==digest)throw new Error('操作回执与原账户、指令或版本不一致，保留原请求待核对。');
 }
 return receipt;
}
export type PolicyAccountType=z.infer<typeof PolicyAccount>;
export type PolicyDetailType=z.infer<typeof PolicyDetail>;
export type PolicyStatusType=z.infer<typeof PolicyStatus>;
export type PendingPolicyType=z.infer<typeof PendingPolicy>;
export const policyStatusLabels:Record<string,string>={DISABLED:'执行器未启用',NO_QUALIFIED_MODELS:'暂无通过准入的模型',WAITING_HISTORY:'等待完整小时历史',WAITING_FRESH_CLOSED_HOUR:'等待新闭合小时',READY:'可执行模拟决策'};
export const policyReasonLabels:Record<string,string>={WAITING_HISTORY:'同源小时历史尚未暖机',WAITING_FRESH_CLOSED_HOUR:'当前没有新鲜闭合小时',WAITING_FORWARD_HOUR:'等待账户启用后的下一个小时',MODEL_NOT_APPROVED_OR_EXPIRED:'模型未获准或已过期',MODEL_NOT_QUALIFIED:'模型未通过准入',POLICY_RUNTIME_DISABLED:'执行器未启用',LOW_LOGIT_MARGIN:'模型动作区分度不足，拒绝决策',INFERENCE_TIMEOUT:'推理超时，未成交',EXPIRED_DECISION:'决策已过期，未成交',MISSING_EXECUTION_QUOTE:'缺少成交报价',QUOTE_BEFORE_DECISION:'报价早于决策',EXCESSIVE_SPREAD:'点差超过账户限制',TARGET_WITHIN_QUANTITY_STEP:'目标已在数量步长内，无需成交',INFERENCE_OR_FEATURE_ERROR:'模型或特征计算异常，未成交'};
