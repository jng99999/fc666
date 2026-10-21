import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GET,POST} from '../app/api/gold/[...segments]/route.ts';
import {comparisonCsv,ResearchMember} from '../lib/gold-research.ts';
const context=segments=>({params:Promise.resolve({segments})});
const id='95cac022-c9dc-49c8-97ca-130b934b2429';
test('research proxy forwards only bounded history resources',async t=>{
 const requests=[];t.mock.method(globalThis,'fetch',async(url,options)=>{requests.push({url:String(url),options});return Response.json({trading_enabled:false});});
 for(const segments of [['research','catalogue'],['research','runs'],['research','runs',id],['research','runs',id,'manifest'],['research','runs',id,'members','12']]){
  assert.equal((await GET(new Request('https://test.example/api/gold/'+segments.join('/')),context(segments))).status,200);
 }
 assert.equal(requests.length,5);
 for(const segments of [['research','runs',id,'members','-1'],['research','runs',id,'members','1000'],['research','private'],['research','runs','bad']]){
  assert.equal((await GET(new Request('https://test.example/api/gold/'+segments.join('/')),context(segments))).status,404);
 }
 assert.equal(requests.length,5);
 assert.equal((await POST(new Request('https://test.example/api/gold/research/runs',{method:'POST',headers:{origin:'https://attacker.example'},body:'{}'}),context(['research','runs']))).status,403);
});
test('member export retains full receipt balances and frozen execution settings',()=>{
 const metrics=Object.fromEntries(['total_return','max_drawdown','final_equity','net_pnl','realized_pnl','unrealized_pnl','fees','spread_cost','slippage_cost','open_ounces','exposure_bar_fraction','return_difference_vs_buy_hold'].map(key=>[key,'0']));
 Object.assign(metrics,{fill_count:1,closed_trades:0,win_rate:null,profit_factor:null,expired_gap_intents:0});
 const input={strategy:'ema_long_flat_v1',timeframe:'1h',window:'full',scenario:'configured_costs',metrics,equity:[],fills:[{side:'BUY',decision_at:1,execution_at:2,bid:'2000',ask:'2001',price:'2001.05',ounces:'1',fee:'.1',spread_cost:'.5',slippage_cost:'.05',realized_pnl:'0',cash_after:'7998.85',quantity_after:'1',simulated:true}],run_id:'a'.repeat(64),manifest:{request:{}},trading_enabled:false,config:{commission:'.1'},requested_start:1,pending_final_signal:'LONG'};
 assert.deepEqual(ResearchMember.parse(input),input);
 assert.equal(ResearchMember.safeParse({...input,trading_enabled:true}).success,false);
});
test('research comparison CSV keeps numerical fractions, provenance and UTC exclusive end',()=>{
 const rows=[{strategy:'ema_long_flat_v1',timeframe:'1h',window:'2025',scenario:'configured_costs',effective_start:Date.UTC(2025,0,1),effective_end:Date.UTC(2026,0,1),metrics:{total_return:'-.05',max_drawdown:'.1',fees:'20',spread_cost:'30',slippage_cost:'4',closed_trades:10,win_rate:'.4',open_ounces:'0'}}];
 const csv=comparisonCsv(rows,'a'.repeat(64));
 assert.ok(csv.startsWith('\ufeff"strategy"'));
 assert.ok(csv.includes('"-.05",".1","20","30","4"'));
 assert.ok(csv.includes('2026-01-01T00:00:00.000Z'));
 assert.ok(csv.includes('"'+ 'a'.repeat(64)+'"'));
 assert.equal(csv.split('\r\n').length,2);
});
