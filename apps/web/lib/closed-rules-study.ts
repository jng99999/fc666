import {z} from 'zod';
import {PriceActionRules} from './directional';

const hash=z.string().regex(/^[0-9a-f]{64}$/),count=z.number().int().nonnegative();
const decimal=z.string().regex(/^-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?$/).refine(value=>Number.isFinite(Number(value)));
export const ClosedRulesStudy=z.object({
 version:z.literal('closed-rules-study-v1'),study_id:hash,protocol_sha256:hash,generated_at:z.iso.datetime({offset:true}),
 trading_enabled:z.literal(false),live_eligible:z.literal(false),historical_news_verified:z.literal(false),
 source_sha256:z.record(z.string(),hash),
 datasets:z.array(z.object({instrument:z.string(),source:z.string(),timeframe:z.string(),window:z.string(),start:z.iso.datetime({offset:true}),end_exclusive:z.iso.datetime({offset:true}),bars:count,dataset_sha256:hash})).min(1),
 results:z.array(z.object({case_id:hash,run_id:hash,instrument:z.string(),timeframe:z.string(),window:z.string(),rule:z.enum(PriceActionRules),scenario:z.enum(['BASE','STRESS']),news_policy:z.enum(['REQUIRE_HISTORICAL','UNCOVERED_RESEARCH']),
  metrics:z.object({initial_cash:decimal,final_equity:decimal,net_pnl:decimal,total_return:decimal,max_drawdown:decimal,fees:decimal,spread_cost:decimal,slippage_cost:decimal,total_cost:decimal,fill_count:count,closed_trade_count:count,open_quantity:decimal}),
  long_entries:count,short_entries:count,stop_exits:count,profit_exits:count,
  costs:z.object({quantity:decimal,fee_rate:decimal,spread:decimal,slippage:decimal,stop_loss_percent:decimal,take_profit_percent:decimal,tick_size:decimal,quantity_step:decimal,max_order_quote:decimal,news_pre_seconds:count,news_post_seconds:count}),
 })).min(1),
 strict_gate:z.object({cases:count,fills:z.literal(0),verified:z.literal(true)}),
 gold_status:z.object({state:z.enum(['AVAILABLE','UNAVAILABLE']),detail:z.string()}),limits:z.array(z.string()).min(1),
 gold_provenance:z.object({page:z.url(),zip_sha256:hash,raw_csv_sha256:hash,zip_bytes:count,raw_csv_bytes:count,ticks:count,complete_minutes:count,incomplete_minutes_excluded:count}).nullable(),
}).superRefine((value,ctx)=>{
 if(new Set(value.results.map(row=>row.case_id)).size!==value.results.length)ctx.addIssue({code:'custom',message:'Duplicate fixed research cases'});
 const strict=value.results.filter(row=>row.news_policy==='REQUIRE_HISTORICAL');
 if(strict.length!==value.strict_gate.cases||strict.some(row=>row.metrics.fill_count!==0||Number(row.metrics.total_return)!==0||Number(row.metrics.fees)!==0))ctx.addIssue({code:'custom',message:'Missing historical news must not produce entries, returns or fees'});
});
export type ClosedRuleStudyRow=z.infer<typeof ClosedRulesStudy>['results'][number];
