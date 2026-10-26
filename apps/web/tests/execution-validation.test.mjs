import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ExecutionValidation} from '../lib/execution-validation.ts';

const data=JSON.parse(readFileSync(new URL('../public/research/execution-validation.json',import.meta.url),'utf8'));
test('published evidence retains all years, losses, unknown news and truncated observations',()=>{
 const parsed=ExecutionValidation.parse(data);
 assert.equal(parsed.mode,'READ_ONLY_RESEARCH');
 assert.equal(parsed.trading_enabled,false);
 assert.equal(parsed.gold.results.filter(row=>row.scenario==='NEWS_REQUIRED_UNAVAILABLE').length,3);
 assert.ok(parsed.gold.results.some(row=>Number(row.return)<0),'loss cases cannot be omitted');
 assert.ok(parsed.gold.results.some(row=>row.position_open_at_end),'unliquidated positions remain disclosed');
 assert.equal(parsed.traders.accounts.reduce((sum,row)=>sum+row.fills,0),11994);
 assert.equal(parsed.traders.live_stream,false);
 assert.equal(parsed.traders.gold_connected,false);
 assert.deepEqual(parsed.traders.accounts.map(row=>row.assets),[0,21,48]);
});
test('unavailable news cannot be repackaged as a trading result',()=>{
 const changed=structuredClone(data);
 changed.gold.results.find(row=>row.scenario==='NEWS_REQUIRED_UNAVAILABLE').long_entries=1;
 assert.equal(ExecutionValidation.safeParse(changed).success,false);
});
test('future-profit claims, missing coverage and malformed fees are rejected',()=>{
 for(const change of [
  value=>{value.trading_enabled=true;},
  value=>{value.traders.live_stream=true;},
  value=>{value.traders.accounts[1].window_complete=true;},
  value=>{value.traders.accounts[1].assets=0;},
  value=>{value.gold.results[0].fees='NaN';},
  value=>{value.gold.results[1]=value.gold.results[0];},
 ]){
  const changed=structuredClone(data);change(changed);
  assert.equal(ExecutionValidation.safeParse(changed).success,false);
 }
});
