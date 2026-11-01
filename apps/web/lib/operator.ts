import {z} from 'zod';

const text=z.string().nullable().transform(value=>value??'');
export const OperatorStatus=z.object({
 initialized:z.boolean(),authenticated:z.boolean(),username:z.string().nullable(),
 csrf_token:z.string().nullable(),config_revision:z.number().int().nonnegative(),trading_enabled:z.literal(false),
}).strict();
export type OperatorSession=z.infer<typeof OperatorStatus>;
export const OperatorConfiguration=z.object({
 revision:z.number().int().nonnegative(),
 model:z.object({provider:z.enum(['','openai','anthropic','google']).nullable().transform(value=>value??''),model:text,base_url:text,api_key_configured:z.boolean()}).strict(),
 news:z.object({source:text,url:text,token_configured:z.boolean()}).strict(),
 gold:z.object({source:text,url:text,token_configured:z.boolean()}).strict(),
 authorized_accounts:z.array(z.uuid()).max(20),trading_enabled:z.literal(false),
}).strict();
export type OperatorConfig=z.infer<typeof OperatorConfiguration>;
export const OperatorAccounts=z.object({accounts:z.array(z.object({account_id:z.uuid(),instrument:z.string(),strategy:z.string(),status:z.string()}).strict()),trading_enabled:z.literal(false)}).strict();
export class OperatorError extends Error{
 constructor(message:string,readonly status:number){super(message);this.name='OperatorError';}
}
export async function operatorRequest<T>(resource:string,schema:z.ZodType<T>,body?:unknown,csrf?:string|null,signal?:AbortSignal):Promise<T>{
 const response=await fetch('/api/admin/'+resource,{
  method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',
  ...(body===undefined?{}:{headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})},body:JSON.stringify(body)}),
  signal:signal?AbortSignal.any([signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000),
 });
 const value:unknown=await response.json();
 if(!response.ok){
  const detail=z.object({detail:z.string().max(240)}).safeParse(value);
  throw new OperatorError(detail.success?detail.data.detail:'管理员服务暂不可用，请重试。',response.status);
 }
 return schema.parse(value);
}

export type ConfigurationDraft={
 provider:string;model:string;base_url:string;api_key:string;clear_api_key:boolean;
 news_source:string;news_url:string;news_token:string;clear_news_token:boolean;
 gold_source:string;gold_url:string;gold_token:string;clear_gold_token:boolean;
 accounts:string;
};
export function draftFromConfig(config:OperatorConfig):ConfigurationDraft{
 return {provider:config.model.provider,model:config.model.model,base_url:config.model.base_url,api_key:'',clear_api_key:false,
  news_source:config.news.source,news_url:config.news.url,news_token:'',clear_news_token:false,
  gold_source:config.gold.source,gold_url:config.gold.url,gold_token:'',clear_gold_token:false,accounts:config.authorized_accounts.join('\n')};
}
function endpoint(raw:string,label:string){
 const value=raw.trim();if(!value)return '';
 let parsed:URL;try{parsed=new URL(value);}catch{throw new Error(label+'需为完整 HTTPS 接口地址。');}
 if(parsed.protocol!=='https:'||!parsed.hostname||parsed.username||parsed.password||parsed.hash||parsed.search||value.length>2048)throw new Error(label+'需为不含用户名、密码、查询参数或片段的 HTTPS 地址。');
 return value;
}
export function configurationPayload(draft:ConfigurationDraft,revision:number,previous?:OperatorConfig){
 const provider=z.enum(['','openai','anthropic','google']).parse(draft.provider),model=draft.model.trim();
 if(Boolean(provider)!==Boolean(model))throw new Error('请同时填写模型服务商和真实模型名，或同时留空。');
 if(model.length>200)throw new Error('模型名不能超过 200 个字符。');
 if(provider==='google'&&draft.base_url.trim())throw new Error('Google 使用官方默认接口；自定义兼容接口请选择 OpenAI。');
 for(const secret of [draft.api_key,draft.news_token,draft.gold_token])if(secret.length>4096||/[\r\n\0]/.test(secret))throw new Error('密钥不能含换行，且不能超过 4096 个字符。');
 const news_source=draft.news_source.trim(),gold_source=draft.gold_source.trim();
 const news_url=endpoint(draft.news_url,'新闻接口'),gold_url=endpoint(draft.gold_url,'黄金历史接口');
 if(Boolean(news_source)!==Boolean(news_url)||Boolean(gold_source)!==Boolean(gold_url))throw new Error('每个数据接口需同时填写来源名称和地址，或同时留空。');
 if(news_source.length>120||gold_source.length>120)throw new Error('来源名称不能超过 120 个字符。');
 if(previous){
  if(previous.model.api_key_configured&&(provider!==previous.model.provider||draft.base_url.trim()!==previous.model.base_url)&&!draft.api_key&&!draft.clear_api_key)throw new Error('更换模型服务商或接口时，需重新填写密钥或勾选清除旧密钥。');
  if(previous.news.token_configured&&news_url!==previous.news.url&&!draft.news_token&&!draft.clear_news_token)throw new Error('更换新闻接口时，需重填 Token 或勾选清除旧 Token。');
  if(previous.gold.token_configured&&gold_url!==previous.gold.url&&!draft.gold_token&&!draft.clear_gold_token)throw new Error('更换黄金接口时，需重填 Token 或勾选清除旧 Token。');
 }
 const accounts=draft.accounts.split(/[\s,]+/).filter(Boolean);
 const authorized_accounts=z.array(z.uuid()).max(20).parse(accounts);
 if(new Set(authorized_accounts).size!==authorized_accounts.length)throw new Error('授权账户不能重复。');
 return {expected_revision:revision,model:{provider,model,base_url:endpoint(draft.base_url,'模型接口'),...(draft.api_key?{api_key:draft.api_key}:{}),clear_api_key:draft.clear_api_key},
  news:{source:news_source,url:news_url,...(draft.news_token?{token:draft.news_token}:{}),clear_token:draft.clear_news_token},
  gold:{source:gold_source,url:gold_url,...(draft.gold_token?{token:draft.gold_token}:{}),clear_token:draft.clear_gold_token},authorized_accounts};
}
