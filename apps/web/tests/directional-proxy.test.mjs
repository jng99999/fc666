import test from 'node:test';
import assert from 'node:assert/strict';
import {GET,POST} from '../app/api/directional/[...segments]/route.ts';
import {GET as health} from '../app/api/health/route.ts';

const params=(segments)=>({params:Promise.resolve({segments})});
const uuid='12345678-1234-4234-8234-123456789012';
test('directional proxy constrains paths, origin and cookies and never retries a write',async t=>{
 const previous=process.env.DIRECTIONAL_API_BASE_URL;
 process.env.DIRECTIONAL_API_BASE_URL='https://backend.example';
 t.after(()=>{if(previous===undefined)delete process.env.DIRECTIONAL_API_BASE_URL;else process.env.DIRECTIONAL_API_BASE_URL=previous;});
 let calls=[];
 t.mock.method(globalThis,'fetch',async(url,options)=>{calls.push({url:String(url),options});return Response.json({trading_enabled:false},{headers:{'set-cookie':'fc666_directional_session='+'B'.repeat(43)+'; HttpOnly; Secure; Path=/api; SameSite=Strict'}});});
 const read=await GET(new Request('https://app.example/api/directional/accounts',{headers:{cookie:'private_secret=hidden; fc666_directional_session='+'A'.repeat(43)}}),params(['accounts']));
 assert.equal(read.status,200);assert.match(read.headers.get('set-cookie'),/HttpOnly/);
 assert.equal(calls[0].options.headers.Cookie,'fc666_directional_session='+'A'.repeat(43));
 assert.equal(calls[0].url,'https://backend.example/api/v1/directional/accounts');
 for(const [segments,headers,status] of [
  [['accounts',uuid,'decision'],{'content-type':'application/json',origin:'https://evil.example'},403],
  [['accounts',uuid,'decision'],{'content-type':'text/plain',origin:'https://app.example'},415],
  [['accounts',uuid,'delete'],{'content-type':'application/json',origin:'https://app.example'},404],
 ]){
  const response=await POST(new Request('https://app.example/api/directional/anything',{method:'POST',headers,body:'{}'}),params(segments));assert.equal(response.status,status);
 }
 assert.equal(calls.length,1);
 t.mock.method(globalThis,'fetch',async()=>{calls.push('failure');throw new TypeError('offline');});
 const response=await POST(new Request('https://app.example/api/directional/accounts',{method:'POST',headers:{origin:'https://app.example','content-type':'application/json'},body:'{}'}),params(['accounts']));
 assert.equal(response.status,502);assert.equal(calls.length,2);assert.equal(response.headers.get('cache-control'),'no-store');
 delete process.env.DIRECTIONAL_API_BASE_URL;
 assert.equal((await GET(new Request('https://app.example/api/directional/status'),params(['status']))).status,503);
 assert.equal(calls.length,2);
});

test('health preserves real upstream 503 and distinguishes proxy failure without retries',async t=>{
 const logs=[];t.mock.method(console,'warn',(...args)=>logs.push(args));
 let calls=0;
 t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({status:'not_ready',trading_enabled:false},{status:503});});
 const unavailable=await health();
 assert.equal(unavailable.status,503);assert.equal(unavailable.headers.get('x-fc666-health-source'),'upstream');
 assert.match(unavailable.headers.get('x-fc666-request-id'),/^[a-f0-9-]{36}$/);
 assert.equal(logs[0][0],'health_upstream_unavailable');assert.equal(calls,1);
 t.mock.method(globalThis,'fetch',async()=>{calls++;throw new TypeError('internal address must not be logged');});
 const failed=await health();
 assert.equal(failed.status,502);assert.equal(failed.headers.get('x-fc666-health-source'),'proxy');assert.equal(calls,2);
 assert.equal(logs[1][0],'health_proxy_failure');assert.doesNotMatch(JSON.stringify(logs),/internal address/);
 assert.equal(failed.headers.get('cache-control'),'no-store');
});
