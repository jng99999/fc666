import {z} from 'zod';

const decimal = z.string().regex(/^-?\d+(?:\.\d+)?$/).refine(value => Number.isFinite(Number(value)));
const count = z.number().int().nonnegative();
const hash = z.string().regex(/^[0-9a-f]{64}$/);
export const ExecutionValidation = z.object({
 version:z.literal('execution-validation-v1'), source_commit:z.string().regex(/^[0-9a-f]{40}$/),
 mode:z.literal('READ_ONLY_RESEARCH'), trading_enabled:z.literal(false),
 tests:z.object({passed:count,warnings:count}), source_sha256:z.record(z.string(),hash),
 gold:z.object({dataset_id:hash,assumptions:z.array(z.string()),results:z.array(z.object({
  year:z.number().int(),scenario:z.enum(['NEWS_REQUIRED_UNAVAILABLE','DIAGNOSTIC_NO_REAL_NEWS']),bars:count,
  long_entries:count,short_entries:count,stop_exits:count,profit_exits:count,return:decimal,news_validated:z.literal(false),
  fees:decimal,position_open_at_end:z.boolean(),
 })).length(6)}),
 traders:z.object({as_of:z.iso.datetime({offset:true}),live_stream:z.literal(false),requests:count,gold_connected:z.literal(false),
  accounts:z.array(z.object({address:z.string().regex(/^0x[0-9a-f]{40}$/),fills:count,assets:count,complete_episodes:count,
   unmatched_fills:count,fees_by_token:z.record(z.string(),decimal),window_complete:z.literal(false),pagination_truncated:z.boolean(),
  })).min(1).max(3),
 }),
}).superRefine((value,ctx)=>{
 const rows=value.gold.results;
 if(new Set(rows.map(row=>`${row.year}:${row.scenario}`)).size!==rows.length)ctx.addIssue({code:'custom',message:'Duplicate research case'});
 for(const row of rows)if(row.scenario==='NEWS_REQUIRED_UNAVAILABLE'&&(row.long_entries+row.short_entries+row.stop_exits+row.profit_exits!==0||Number(row.return)!==0||Number(row.fees)!==0))ctx.addIssue({code:'custom',message:'Unavailable news must not produce trades'});
 for(const account of value.traders.accounts)if(account.unmatched_fills>account.fills||(account.fills>0&&account.assets===0))ctx.addIssue({code:'custom',message:'Observed fill coverage inconsistent'});
});
