import test from 'node:test';
import assert from 'node:assert/strict';
import {derivativeSymbol,supportsDerivatives,parseDerivatives,validateDerivativesSnapshot,derivativesStale,fetchDerivativesSnapshot} from '../lib/chart-derivatives.ts';

const now=1794701364000;
function interest(){return {code:'00000',requestTime:now-100,data:{list:[{symbol:'BTCUSDT',openInterest:'36700.257900000073'}],ts:String(now-100)}};}
function funding(){return {code:'00000',requestTime:now-50,data:[{symbol:'BTCUSDT',fundingRate:'-0.000013',fundingRateInterval:'8',nextUpdate:String(now+7200000)}]};}
test('only exact official source and eight Bitget spot identities enable corresponding futures',()=>{
 assert.equal(derivativeSymbol('bitget:spot:BTC-USDT'),'BTCUSDT');
 assert.equal(derivativeSymbol('bitget:spot:AVAX-USDT'),'AVAXUSDT');
 for(const id of ['XAU/USD','binance:spot:BTC-USDT','bitget:spot:BTC-USDC','bitget:spot:BTC-USDT?host=evil','https://evil.example'])assert.equal(derivativeSymbol(id),null);
 assert.equal(supportsDerivatives({instrumentId:'bitget:spot:BTC-USDT',source:'bitget.public',timeframe:'1m',volumeKind:'trade'}),true);
 assert.equal(supportsDerivatives({instrumentId:'bitget:spot:BTC-USDT',source:'untrusted',timeframe:'1m',volumeKind:'trade'}),false);
});
test('real documented v3 shape retains legal negative funding rate and timestamps',()=>{
 const value=parseDerivatives(interest(),funding(),'BTCUSDT',now);
 assert.equal(value.openInterest,'36700.257900000073');assert.equal(value.fundingRate,'-0.000013');assert.equal(value.fundingIntervalHours,8);assert.equal(value.baseAsset,'BTC');assert.equal(value.category,'USDT-FUTURES');assert.equal(value.fetchedAt,now);
 assert.deepEqual(validateDerivativesSnapshot(value,'BTCUSDT',now),value);assert.equal(derivativesStale(value,now),false);assert.equal(derivativesStale(value,now+120001),true);
});
test('wrong symbols, unsuccessful codes, missing and ambiguous rows are rejected',()=>{
 const badSymbol=interest();badSymbol.data.list[0].symbol='ETHUSDT';assert.throws(()=>parseDerivatives(badSymbol,funding(),'BTCUSDT',now),/不匹配/);
 const badFunding=funding();badFunding.data[0].symbol='ETHUSDT';assert.throws(()=>parseDerivatives(interest(),badFunding,'BTCUSDT',now),/不匹配/);
 assert.throws(()=>parseDerivatives({...interest(),code:'40017'},funding(),'BTCUSDT',now),/成功/);
 assert.throws(()=>parseDerivatives({...interest(),data:{list:[]}},funding(),'BTCUSDT',now),/唯一/);
 const duplicate=funding();duplicate.data.push({...duplicate.data[0]});assert.throws(()=>parseDerivatives(interest(),duplicate,'BTCUSDT',now),/唯一/);
});
test('stale, future and expired settlement timestamps cannot be called fresh',()=>{
 for(const stamp of [now-120001,now+5001]){const data=interest();data.data.ts=String(stamp);assert.throws(()=>parseDerivatives(data,funding(),'BTCUSDT',now),/过期|异常/);}
 const stale=funding();stale.requestTime=now-120001;assert.throws(()=>parseDerivatives(interest(),stale,'BTCUSDT',now),/过期/);
 const expired=funding();expired.data[0].nextUpdate=String(now-100);assert.throws(()=>parseDerivatives(interest(),expired,'BTCUSDT',now),/结算/);
 const snapshot=parseDerivatives(interest(),funding(),'BTCUSDT',now);assert.throws(()=>validateDerivativesSnapshot(snapshot,'ETHUSDT',now),/不匹配/);assert.throws(()=>validateDerivativesSnapshot({...snapshot,source:'other'},'BTCUSDT',now),/不匹配/);
});
test('nonfinite, malformed or negative OI and invalid funding intervals fail closed',()=>{
 for(const raw of ['NaN','Infinity','1e999','-1','']){const data=interest();data.data.list[0].openInterest=raw;assert.throws(()=>parseDerivatives(data,funding(),'BTCUSDT',now),/数值/);}
 for(const raw of ['0','-8','8.5','Infinity']){const data=funding();data.data[0].fundingRateInterval=raw;assert.throws(()=>parseDerivatives(interest(),data,'BTCUSDT',now),/周期/);}
 const overflow=funding();overflow.data[0].fundingRate='1e308';assert.throws(()=>parseDerivatives(interest(),overflow,'BTCUSDT',now),/范围/);
});
test('public fetch uses fixed hosts, no redirects/cookies/auth and no-store for both resources',async()=>{
 const calls=[];const fetcher=async(url,options)=>{calls.push({url,options});return Response.json(url.pathname.endsWith('open-interest')?interest():funding());};
 const result=await fetchDerivativesSnapshot('BTCUSDT',fetcher,()=>now);
 assert.equal(result.symbol,'BTCUSDT');assert.equal(calls.length,2);
 for(const {url,options} of calls){assert.equal(url.origin,'https://api.bitget.com');assert.equal(url.searchParams.get('symbol'),'BTCUSDT');assert.equal(url.searchParams.get('category'),'USDT-FUTURES');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(options.credentials,'omit');assert.deepEqual(options.headers,{Accept:'application/json'});assert.ok(options.signal);}
});
test('HTTP errors, upstream broken JSON and oversized responses are not silently replaced',async()=>{
 await assert.rejects(()=>fetchDerivativesSnapshot('BTCUSDT',async()=>new Response('',{status:429}),()=>now),/HTTP 429/);
 await assert.rejects(()=>fetchDerivativesSnapshot('BTCUSDT',async()=>new Response('bad json'),()=>now),/JSON/);
 await assert.rejects(()=>fetchDerivativesSnapshot('BTCUSDT',async()=>new Response('x'.repeat(64001)),()=>now),/过大/);
});
test('timeouts abort the public requests instead of hanging or returning fake data',async()=>{
 const timer=setTimeout(()=>{},1000);
 const fetcher=async(_url,options)=>new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true});});
 try{await assert.rejects(()=>fetchDerivativesSnapshot('BTCUSDT',fetcher,()=>now,10),error=>error.name==='TimeoutError');}finally{clearTimeout(timer);}
});
