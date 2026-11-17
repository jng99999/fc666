import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GET,POST} from '../app/api/gold/[...segments]/route.ts';
const context=segments=>({params:Promise.resolve({segments})});
test('gold proxy accepts actual Host origin when Next URL uses localhost',async t=>{
 let sent;
 t.mock.method(globalThis,'fetch',async(url,options)=>{sent={url:String(url),options};return Response.json({trading_enabled:false},{headers:{'Set-Cookie':'fc666_gold_session=example; HttpOnly; Secure; Path=/api'}});});
 const request=new Request('http://localhost:3017/api/gold/accounts',{method:'POST',headers:{host:'127.0.0.1:3017',origin:'http://127.0.0.1:3017','content-type':'application/json',cookie:`unrelated=do-not-forward; fc666_gold_session=${'x'.repeat(43)}`},body:'{}'});
 const result=await POST(request,context(['accounts']));
 assert.equal(result.status,200);assert.equal(sent.options.headers.Cookie,`fc666_gold_session=${'x'.repeat(43)}`);
 assert.equal(sent.options.method,'POST');assert.equal(result.headers.get('cache-control'),'no-store');assert.ok(result.headers.get('set-cookie').includes('HttpOnly'));
});
test('gold proxy rejects external or different scheme origins without forwarding',async t=>{
 let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({});});
 for(const origin of ['https://attacker.example','https://127.0.0.1:3017','null']){
  const result=await POST(new Request('http://localhost:3017/api/gold/accounts',{method:'POST',headers:{host:'127.0.0.1:3017',origin},body:'{}'}),context(['accounts']));assert.equal(result.status,403);
 }
 assert.equal(calls,0);
});
test('gold proxy restricts routes and payload size',async()=>{
 assert.equal((await GET(new Request('https://example.com/api/gold/private/orders'),context(['private','orders']))).status,404);
 assert.equal((await POST(new Request('https://example.com/api/gold/accounts',{method:'POST',body:'x'.repeat(8193)}),context(['accounts']))).status,413);
});
