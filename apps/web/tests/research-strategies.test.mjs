import {test} from 'node:test';
import assert from 'node:assert/strict';
import {StrategyCatalog,StrategyIds,StrategyVariant,strategyLookback} from '../lib/research-strategies.ts';
import {Symbols,MarketProvider} from '../lib/market.ts';
test('all seven catalog defaults satisfy their request contracts',()=>{
 assert.equal(StrategyIds.length,7);
 for(const strategy of StrategyIds)assert.equal(StrategyVariant.safeParse({strategy,parameters:StrategyCatalog[strategy].defaults}).success,true);
});
test('invalid strategy parameters never become submit-ready',()=>{
 for(const row of [
  {strategy:'rsi_long_flat_v1',parameters:{period:14,lower:70,upper:30}},
  {strategy:'sma_long_flat_v1',parameters:{fast:20,slow:10}},
  {strategy:'channel_long_flat_v1',parameters:{entry:10,exit:20}},
  {strategy:'bollinger_long_flat_v1',parameters:{period:20,deviations:0}},
  {strategy:'ema_long_flat_v1',parameters:{period:null}},
  {strategy:'ema_long_flat_v1',parameters:{period:2.1}},
  {strategy:'macd_long_flat_v1',parameters:{fast:26,slow:12,signal:9}},
  {strategy:'macd_long_flat_v1',parameters:{fast:12,slow:26,signal:1}},
  {strategy:'roc_long_flat_v1',parameters:{period:0}},
  {strategy:'roc_long_flat_v1',parameters:{period:20,threshold:1}},
  {strategy:'unknown',parameters:{period:20}},
 ])assert.equal(StrategyVariant.safeParse(row).success,false);
});
test('Bitget universe is bounded and unique',()=>{
 assert.equal(new Set(Symbols).size,Symbols.length);
 assert.equal(Symbols.length,MarketProvider==='bitget'?8:2);
 if(MarketProvider==='bitget')assert.ok(Symbols.some(symbol=>symbol==='SOLUSDT'));
});
test('strategy warmup matches independent segment execution requirements',()=>{
 const expected=[20,20,15,20,21,34,21];
 StrategyIds.forEach((strategy,index)=>assert.equal(strategyLookback(StrategyVariant.parse({strategy,parameters:StrategyCatalog[strategy].defaults})),expected[index]));
 assert.equal(strategyLookback(StrategyVariant.parse({strategy:'macd_long_flat_v1',parameters:{fast:2,slow:3,signal:2}})),4);
});
