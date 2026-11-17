import {z} from 'zod';
const period=z.number().int().min(2).max(500);
export const StrategyIds=['ema_long_flat_v1','sma_long_flat_v1','rsi_long_flat_v1','bollinger_long_flat_v1','channel_long_flat_v1','macd_long_flat_v1','roc_long_flat_v1','stochastic_long_flat_v1'] as const;
export type StrategyId=typeof StrategyIds[number];
export const StrategyCatalog={
 ema_long_flat_v1:{name:'EMA 趋势跟随',defaults:{period:20},description:'收盘高于 EMA 做多，否则空仓。适合观察趋势，震荡中容易反复交易。'},
 sma_long_flat_v1:{name:'SMA 双均线',defaults:{fast:10,slow:20},description:'快均线高于慢均线做多，否则空仓。信号存在滞后。'},
 rsi_long_flat_v1:{name:'RSI 均值回归',defaults:{period:14,lower:30,upper:70},description:'Wilder RSI ≤ 下阈值做多，≥ 上阈值退出，中间持有。单边下跌可能持续亏损。'},
 bollinger_long_flat_v1:{name:'布林带回归',defaults:{period:20,deviations:2},description:'收盘跌破下轨做多，回到中轨退出。使用总体标准差，零波动不入场。'},
 channel_long_flat_v1:{name:'价格通道突破',defaults:{entry:20,exit:10},description:'收盘突破此前 N 根最高价做多，跌破此前 M 根最低价退出。阈值不含当前柱。'},
 macd_long_flat_v1:{name:'MACD 信号线趋势',defaults:{fast:12,slow:26,signal:9},description:'MACD 高于信号线做多，低于或等于信号线空仓。EMA 用 SMA 初始化，需慢周期＋信号周期－1 根柱预热；震荡中易反复交易。'},
 roc_long_flat_v1:{name:'ROC 零轴动量',defaults:{period:20},description:'收盘价高于 N 根前收盘价做多，否则空仓。需 N＋1 根柱预热，零收益不入场；趋势反转和震荡可能亏损。'},
 stochastic_long_flat_v1:{name:'Stochastic 随机指标回归',defaults:{period:14,smooth:3,lower:20,upper:80},description:'对区间内收盘位置 %K 做简单均线平滑，≤下阈值做多、≥上阈值退出。零价格区间不发信号并重置平滑；单边下跌有持续亏损风险。'},
} satisfies Record<StrategyId,{name:string;defaults:Record<string,number>;description:string}>;
export const ParameterLabels:Record<string,string>={period:'周期',fast:'快周期',slow:'慢周期',signal:'信号周期',smooth:'平滑周期',lower:'下阈值',upper:'上阈值',deviations:'标准差倍数',entry:'突破窗口',exit:'退出窗口'};
export const StrategyVariant=z.discriminatedUnion('strategy',[
 z.object({strategy:z.literal('ema_long_flat_v1'),parameters:z.object({period}).strict()}).strict(),
 z.object({strategy:z.literal('sma_long_flat_v1'),parameters:z.object({fast:period,slow:period}).strict()}).strict(),
 z.object({strategy:z.literal('rsi_long_flat_v1'),parameters:z.object({period,lower:z.number().int().min(1).max(99),upper:z.number().int().min(1).max(99)}).strict()}).strict(),
 z.object({strategy:z.literal('bollinger_long_flat_v1'),parameters:z.object({period,deviations:z.number().int().min(1).max(5)}).strict()}).strict(),
 z.object({strategy:z.literal('channel_long_flat_v1'),parameters:z.object({entry:period,exit:period}).strict()}).strict(),
 z.object({strategy:z.literal('macd_long_flat_v1'),parameters:z.object({fast:period,slow:period,signal:period}).strict()}).strict(),
 z.object({strategy:z.literal('roc_long_flat_v1'),parameters:z.object({period}).strict()}).strict(),
 z.object({strategy:z.literal('stochastic_long_flat_v1'),parameters:z.object({period,smooth:period,lower:z.number().int().min(1).max(99),upper:z.number().int().min(1).max(99)}).strict()}).strict(),
]).refine(row=>row.strategy==='sma_long_flat_v1'||row.strategy==='macd_long_flat_v1'?row.parameters.fast<row.parameters.slow:row.strategy==='rsi_long_flat_v1'||row.strategy==='stochastic_long_flat_v1'?row.parameters.lower<row.parameters.upper:row.strategy==='channel_long_flat_v1'?row.parameters.exit<=row.parameters.entry:true);

export function strategyLookback(row:z.infer<typeof StrategyVariant>):number{
 switch(row.strategy){
  case 'macd_long_flat_v1':return row.parameters.slow+row.parameters.signal-1;
  case 'stochastic_long_flat_v1':return row.parameters.period+row.parameters.smooth-1;
  case 'sma_long_flat_v1':return row.parameters.slow;
  case 'channel_long_flat_v1':return row.parameters.entry+1;
  case 'rsi_long_flat_v1':case 'roc_long_flat_v1':return row.parameters.period+1;
  case 'ema_long_flat_v1':case 'bollinger_long_flat_v1':return row.parameters.period;
 }
}
