import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildSync} from 'esbuild';

const bundled=buildSync({entryPoints:[new URL('../lib/closed-rules-study.ts',import.meta.url).pathname],bundle:true,platform:'node',format:'esm',write:false});
const {ClosedRulesStudy}=await import('data:text/javascript;base64,'+Buffer.from(bundled.outputFiles[0].contents).toString('base64'));

const saved=JSON.parse(readFileSync(new URL('../public/research/closed-rules-backtest.json',import.meta.url),'utf8'));
test('published actual fixed study contains all cases and never enables trading',()=>{
 const report=ClosedRulesStudy.parse(saved);
 assert.equal(report.results.length,120);assert.equal(report.strict_gate.cases,60);
 assert.equal(report.strict_gate.fills,0);assert.equal(report.gold_status.state,'AVAILABLE');
 assert.equal(report.live_eligible,false);assert.equal(report.historical_news_verified,false);
 assert.equal(JSON.stringify(saved).includes('/Users/'),false);
});
test('a modified strict-news return cannot be displayed as a valid study',()=>{
 const value=structuredClone(saved),row=value.results.find(row=>row.news_policy==='REQUIRE_HISTORICAL');
 row.metrics.total_return='0.1';assert.equal(ClosedRulesStudy.safeParse(value).success,false);
 row.metrics.total_return='0';row.metrics.fill_count=1;assert.equal(ClosedRulesStudy.safeParse(value).success,false);
});
test('duplicate cases or fabricated live eligibility fail the public report contract',()=>{
 const duplicate=structuredClone(saved);duplicate.results.push(duplicate.results[0]);
 assert.equal(ClosedRulesStudy.safeParse(duplicate).success,false);
 const enabled=structuredClone(saved);enabled.live_eligible=true;
 assert.equal(ClosedRulesStudy.safeParse(enabled).success,false);
});
