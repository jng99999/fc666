import {z} from "zod";
export const Symbols = ["BTCUSDT","ETHUSDT"] as const;
export const Intervals = ["1m","5m","15m","1h","4h","1d"] as const;
export type SymbolName = typeof Symbols[number];
export type Interval = typeof Intervals[number];
const decimal = z.string().regex(/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/).refine(v => Number.isFinite(Number(v)));
const positive = decimal.refine(v => Number(v)>0);
const nonnegative = decimal.refine(v => Number(v)>=0);
export const CandleSchema = z.object({instrument_id:z.string(),timeframe:z.enum(Intervals),open_time:z.iso.datetime({offset:true}),close_time:z.iso.datetime({offset:true}),open:positive,high:positive,low:positive,close:positive,volume:nonnegative,is_closed:z.boolean(),source:z.literal("binance.public")});
export type Candle = z.infer<typeof CandleSchema>;
const Base = z.object({schema_version:z.literal(1),type:z.literal("snapshot"),symbol:z.enum(Symbols),instrument_id:z.string(),generation:z.string(),sequence:z.number().int().positive(),received_at:z.iso.datetime({offset:true}),event_time:z.iso.datetime({offset:true}).nullable(),quality:z.literal("healthy")});
const Ticker = Base.extend({channel:z.literal("ticker"),payload:z.object({price:positive,change_percent:decimal,volume:nonnegative,event_time:z.iso.datetime({offset:true})})});
const Book = Base.extend({channel:z.literal("book"),payload:z.object({valid:z.literal(true),exchange_sequence:z.number().int().nonnegative(),snapshot_depth_limit:z.number(),bids:z.array(z.tuple([positive,nonnegative])).max(20),asks:z.array(z.tuple([positive,nonnegative])).max(20)})});
const Trade = Base.extend({channel:z.literal("trade"),payload:z.object({trades:z.array(z.object({trade_id:z.number().int().nonnegative(),price:positive,quantity:positive,aggressor_side:z.enum(["BUY","SELL"]),event_time:z.iso.datetime({offset:true})})).max(100)})});
const LiveCandle = Base.extend({channel:z.enum(Intervals.map(i => `candle.${i}` as const)),payload:CandleSchema});
export const SnapshotSchema = z.discriminatedUnion("channel",[Ticker,Book,Trade,LiveCandle]);
export type Snapshot = z.infer<typeof SnapshotSchema>;
export type TickerSnapshot = z.infer<typeof Ticker>;
export type BookSnapshot = z.infer<typeof Book>;
export type TradeSnapshot = z.infer<typeof Trade>;
export type CandleSnapshot = z.infer<typeof LiveCandle>;
export async function getCandles(symbol:SymbolName,timeframe:Interval,signal:AbortSignal,limit=1000): Promise<Candle[]> {
  const response=await fetch(`/api/market/candles?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`,{signal,cache:"no-store"});
  if (!response.ok) throw new Error("历史数据服务不可用");
  return z.array(CandleSchema).parse(await response.json());
}
export const keyFor = (symbol:SymbolName,channel:string) => `${symbol}:${channel}`;

const IndicatorValues=z.object({sma:z.number().finite().nullable(),ema:z.number().finite().nullable(),bollinger_upper:z.number().finite().nullable(),bollinger_lower:z.number().finite().nullable(),vwap:z.number().finite().nullable(),rsi:z.number().finite().nullable(),atr:z.number().finite().nullable(),macd:z.number().finite().nullable(),macd_signal:z.number().finite().nullable(),macd_histogram:z.number().finite().nullable()});
export const IndicatorSchema=z.object({schema_version:z.literal(1),closed_only:z.literal(true),seed_start:z.iso.datetime({offset:true}).nullable(),rows:z.array(z.object({open_time:z.iso.datetime({offset:true}),available_at:z.iso.datetime({offset:true}),regime:z.object({rule_version:z.literal("er-atr-v1"),label:z.enum(["UNAVAILABLE","RANGE","TREND_UP","TREND_DOWN"]),volatility:z.enum(["UNAVAILABLE","LOW","NORMAL","HIGH"]),efficiency_ratio:z.number().finite().nullable(),atr_fraction:z.number().finite().nullable(),available_at:z.iso.datetime({offset:true})}),values:IndicatorValues,confirmed_swings:z.array(z.object({kind:z.enum(["high","low"]),price:decimal,pivot_time:z.iso.datetime({offset:true}),confirmed_at:z.iso.datetime({offset:true}),label:z.string().nullable()})),structure_breaks:z.array(z.object({direction:z.enum(["up","down"]),kind:z.enum(["BOS","CHOCH"]),level:decimal,available_at:z.iso.datetime({offset:true})}))}))});
export type IndicatorRow=z.infer<typeof IndicatorSchema>["rows"][number];
export async function getIndicators(symbol:SymbolName,timeframe:Interval,signal:AbortSignal,limit=1000,period=20){
  const response=await fetch(`/api/market/indicators?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}&period=${period}`,{signal,cache:"no-store"});
  if(!response.ok)throw new Error(response.status===409?"历史数据有缺口，指标不可用":"指标服务不可用");
  return IndicatorSchema.parse(await response.json());
}
