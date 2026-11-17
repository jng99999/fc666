import {z} from 'zod';
import {Symbols} from './market';
import {StrategyVariant,strategyLookback} from './research-strategies';
export const TimeframeSeconds={'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400} as const;
export const Timeframes=['1m','5m','15m','1h','4h','1d'] as const;
const date=z.iso.datetime({offset:true});
export const MatrixInput=z.object({request_id:z.uuid(),symbols:z.array(z.enum(Symbols)).min(1).max(Symbols.length),timeframes:z.array(z.enum(Timeframes)).min(1).max(6),
 variants:z.array(StrategyVariant).min(1).max(8),start_at:date,as_of:date,config:z.record(z.string(),z.unknown())}).strict().superRefine((row,ctx)=>{
 const fail=(message:string)=>ctx.addIssue({code:'custom',message});
 if(new Set(row.symbols).size!==row.symbols.length||new Set(row.timeframes).size!==row.timeframes.length)fail('产品或周期重复');
 if(new Set(row.variants.map(v=>JSON.stringify(v))).size!==row.variants.length)fail('策略参数重复');
 const start=Date.parse(row.start_at),end=Date.parse(row.as_of),count=row.symbols.length*row.timeframes.length*row.variants.length;
 if(!(start<end&&end<=Date.now()))fail('请选择过去且递增的 UTC 日期');
 if(count>20)fail('一次最多 20 个任务，请减少产品、周期或策略');
 let total=0;
 for(const timeframe of row.timeframes){
  const step=TimeframeSeconds[timeframe]*1000,bars=(end-start)/step;
  if(start%step||end%step||!Number.isInteger(bars)||bars<2||bars>5000)fail(`${timeframe} 需要完整的 2–5000 根柱，请缩短日期范围或使用更长周期`);
  if(row.variants.some(v=>bars<strategyLookback(v)+1))fail(`${timeframe} 数据不足以完成策略预热和下一柱成交`);
  total+=bars*row.symbols.length*row.variants.length;
 }
 if(total>20000)fail('批量总柱数超过 20000，请减少范围');
});
const decimal=z.string().refine(v=>v.trim()!==''&&Number.isFinite(Number(v)));
export const MatrixResult=z.object({matrix_id:z.uuid(),request_sha256:z.string(),status:z.enum(['ACTIVE','COMPLETE']),trading_enabled:z.literal(false),scope:z.object({start_at:z.string(),as_of:z.string()}).passthrough(),
 jobs:z.array(z.object({job_id:z.uuid(),status:z.enum(['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED']),progress:z.number(),cancel_requested:z.boolean(),error:z.string().nullable(),run_id:z.string().nullable(),
 request:z.object({symbol:z.enum(Symbols),timeframe:z.enum(Timeframes),strategy:z.string(),parameters:z.record(z.string(),z.unknown()),limit:z.number().int(),window_start:z.string(),as_of:z.string(),dataset_sha256:z.string()}).passthrough(),
 metrics:z.object({total_return:decimal,max_drawdown:decimal,fees:decimal,net_pnl:decimal,fill_count:z.number()}).passthrough().nullable(),sharpe:z.number().finite().nullable(),closed_trade_count:z.number().nullable()})).min(1).max(20)});
