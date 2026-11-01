import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildSync} from 'esbuild';
const bundled=buildSync({entryPoints:[new URL('../lib/research-matrix.ts',import.meta.url).pathname],bundle:true,platform:'node',format:'esm',write:false});
const {MatrixInput}=await import('data:text/javascript;base64,'+Buffer.from(bundled.outputFiles[0].contents).toString('base64'));
const base={request_id:'00000000-0000-4000-8000-000000000001',symbols:['BTCUSDT','ETHUSDT'],timeframes:['4h','1d'],variants:[{strategy:'stochastic_long_flat_v1',parameters:{period:14,smooth:3,lower:20,upper:80}}],start_at:'2025-01-01T00:00:00Z',as_of:'2025-07-01T00:00:00Z',config:{}};
test('same-calendar six-month multi-market window accepts >1000 bars at 4h',()=>assert.equal(MatrixInput.safeParse(base).success,true));
test('oversized task grid, tiny windows, missing products and duplicate axes are blocked',()=>{
 for(const value of [
  {...base,symbols:[]},{...base,timeframes:['4h','4h']},{...base,symbols:['BTCUSDT','BTCUSDT']},
  {...base,timeframes:['1m']},{...base,start_at:base.as_of},{...base,start_at:'2025-01-01T01:00:00Z'},
  {...base,variants:[base.variants[0],base.variants[0]]},
  {...base,symbols:['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT'],timeframes:['1h','4h','1d']},
  {...base,start_at:'2025-06-30T00:00:00Z'},
  {...base,variants:[{strategy:'stochastic_long_flat_v1',parameters:{period:14,smooth:3,lower:80,upper:20}}]},
 ])assert.equal(MatrixInput.safeParse(value).success,false);
});
