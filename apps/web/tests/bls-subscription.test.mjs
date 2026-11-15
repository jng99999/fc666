import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {GET} from '../app/api/public-calendar/bls/route.ts';

test('fixed BLS subscription preserves original fetch evidence, caches, bounds and fails closed',async t=>{
 let now=Date.parse('2026-11-15T22:00:00Z'),mode='valid';const calls=[];
 t.mock.method(Date,'now',()=>now);
 t.mock.method(globalThis,'fetch',async(url,options)=>{
  calls.push({url,options});
  if(mode==='403')return new Response('Access denied',{status:403});
  const text=mode==='oversize'?'x'.repeat(1_500_001):url.endsWith('.ics')?'BEGIN:VCALENDAR\nEND:VCALENDAR\n':'<html><title>Schedule November 2026</title></html>';
  return new Response(text,{headers:{date:new Date(now).toUTCString(),'last-modified':'Sun, 01 Nov 2026 00:00:00 GMT'}});
 });
 const request=()=>new Request('https://paper.example/api/public-calendar/bls');
 assert.equal((await GET(new Request(request().url+'?url=https://private.invalid'))).status,400);assert.equal(calls.length,0);
 const first=await GET(request());assert.equal(first.status,200);const value=await first.json();
 assert.equal(value.scope_month,'2026-11');assert.equal(calls.length,2);
 assert.deepEqual(calls.map(row=>row.url),['https://www.bls.gov/schedule/news_release/bls.ics','https://www.bls.gov/schedule/2026/11_sched.htm']);
 assert.ok(calls.every(row=>row.options.redirect==='error'&&row.options.cache==='no-store'));
 assert.equal(value.resources.calendar.sha256,createHash('sha256').update(value.resources.calendar.content).digest('hex'));
 now+=30_000;assert.deepEqual(await (await GET(request())).json(),value);assert.equal(calls.length,2);
 now+=31_000;mode='403';assert.equal((await GET(request())).status,503);
 const afterFailure=calls.length;now+=30_000;assert.equal((await GET(request())).status,503);assert.equal(calls.length,afterFailure);
 now+=31_000;mode='oversize';assert.equal((await GET(request())).status,503);
});
