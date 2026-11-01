import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const output=await build({entryPoints:[fileURLToPath(new URL('../app/api/admin/[...segments]/route.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'});
const module={exports:{}};new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
const {GET,POST}=module.exports;
const origin='https://fc666-test.vercel.app',token='a'.repeat(43),csrf='b'.repeat(64);
const status={initialized:true,authenticated:true,username:'operator',csrf_token:csrf,config_revision:0,trading_enabled:false};
function request(resource,{method='POST',body='{}',headers={}}={}){return new Request(origin+'/api/admin/'+resource,{method,...(method==='GET'?{}:{body}),headers:{Host:'fc666-test.vercel.app',Origin:origin,'Content-Type':'application/json',Cookie:`fc666_admin_session=${token}; fc666_directional_session=${'c'.repeat(43)}; unrelated=DO_NOT_FORWARD`,'X-CSRF-Token':csrf,...headers}});}
const context=resource=>({params:Promise.resolve({segments:resource.split('/')})});
const config={revision:0,model:{provider:'',model:'',base_url:'',api_key_configured:false},news:{source:'',url:'',token_configured:false},gold:{source:'',url:'',token_configured:false},authorized_accounts:[],trading_enabled:false};

test('administrator credentials never travel to a non-TLS remote or credential-bearing upstream URL',async()=>{
 const oldBase=process.env.DIRECTIONAL_API_BASE_URL,oldFetch=globalThis.fetch;let calls=0;
 globalThis.fetch=async()=>{calls++;throw new Error('must not send credentials');};
 try{
  for(const base of ['http://remote.example','https://name:secret@private.example','https://private.example?token=secret','not a URL']){
   process.env.DIRECTIONAL_API_BASE_URL=base;
   assert.equal((await POST(request('login'),context('login'))).status,503);
  }
  assert.equal(calls,0);
 }finally{globalThis.fetch=oldFetch;if(oldBase===undefined)delete process.env.DIRECTIONAL_API_BASE_URL;else process.env.DIRECTIONAL_API_BASE_URL=oldBase;}
});

test('administrator proxy denies unsupported, cross-origin, oversized and unauthenticated requests without contacting upstream',async()=>{
 const oldBase=process.env.DIRECTIONAL_API_BASE_URL,oldFetch=globalThis.fetch;process.env.DIRECTIONAL_API_BASE_URL='https://private-directional.example';let calls=0;globalThis.fetch=async()=>{calls++;throw new Error('must not call');};
 try{
  assert.equal((await POST(request('configuration',{headers:{Origin:'https://evil.example'}}),context('configuration'))).status,403);
  assert.equal((await POST(request('configuration',{headers:{'Sec-Fetch-Site':'cross-site'}}),context('configuration'))).status,403);
  assert.equal((await POST(request('configuration',{headers:{'X-CSRF-Token':''}}),context('configuration'))).status,403);
  assert.equal((await GET(request('configuration',{method:'GET',headers:{Cookie:''}}),context('configuration'))).status,401);
  assert.equal((await POST(request('configuration',{body:'x'.repeat(16385)}),context('configuration'))).status,413);
  assert.equal((await POST(request('configuration',{headers:{'Content-Type':'text/plain'}}),context('configuration'))).status,415);
  assert.equal((await GET(request('other',{method:'GET'}),context('other'))).status,404);
  assert.equal((await GET(request('configuration/extra',{method:'GET'}),context('configuration/extra'))).status,404);
  assert.equal(calls,0);
 }finally{globalThis.fetch=oldFetch;if(oldBase===undefined)delete process.env.DIRECTIONAL_API_BASE_URL;else process.env.DIRECTIONAL_API_BASE_URL=oldBase;}
});

test('proxy forwards only scoped cookies and CSRF, reconstructs the secure administrator cookie and never reflects upstream errors',async()=>{
 const oldBase=process.env.DIRECTIONAL_API_BASE_URL,oldFetch=globalThis.fetch;process.env.DIRECTIONAL_API_BASE_URL='https://private-directional.example';let observed;
 globalThis.fetch=async(url,options)=>{observed={url:String(url),options};return new Response(JSON.stringify(status),{headers:{'Set-Cookie':`fc666_admin_session=${token}; Domain=evil.example; Path=/bad; Max-Age=99999999; SameSite=None`}});};
 try{
  const response=await POST(request('login'),context('login'));
  assert.equal(response.status,200);assert.equal(observed.url,'https://private-directional.example/api/v1/directional/admin/login');
  assert(!observed.options.headers.Cookie.includes('unrelated'));assert.equal(observed.options.headers['X-CSRF-Token'],csrf);assert.equal(observed.options.redirect,'error');
  assert.equal(response.headers.get('set-cookie'),`fc666_admin_session=${token}; Path=/; Max-Age=28800; Secure; HttpOnly; SameSite=Strict`);
  assert.equal(response.headers.get('cache-control'),'no-store');
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:'secret_model_key_should_not_be_reflected'}),{status:422});
  const failed=await POST(request('configuration'),context('configuration'));assert.equal(failed.status,422);assert(!(await failed.text()).includes('secret_model_key'));
  globalThis.fetch=async()=>new Response(JSON.stringify({...config,model:{...config.model,api_key:'secret_in_bad_response'}}));
  const bad=await GET(request('configuration',{method:'GET'}),context('configuration'));assert.equal(bad.status,502);assert(!(await bad.text()).includes('secret_in_bad_response'));
 }finally{globalThis.fetch=oldFetch;if(oldBase===undefined)delete process.env.DIRECTIONAL_API_BASE_URL;else process.env.DIRECTIONAL_API_BASE_URL=oldBase;}
});

test('logout cookies are scoped deletions and unknown upstream cookies are ignored',async()=>{
 const oldBase=process.env.DIRECTIONAL_API_BASE_URL,oldFetch=globalThis.fetch;process.env.DIRECTIONAL_API_BASE_URL='https://private-directional.example';
 try{
  for(const deletion of ['fc666_admin_session=; Max-Age=0; Path=/','fc666_admin_session=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Max-Age=0; Path=/; SameSite=strict']){
   globalThis.fetch=async()=>new Response(JSON.stringify({...status,authenticated:false,username:null,csrf_token:null}),{headers:{'Set-Cookie':deletion}});
   const logout=await POST(request('logout'),context('logout'));assert.equal(logout.headers.get('set-cookie'),'fc666_admin_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict');
  }
  globalThis.fetch=async()=>new Response(JSON.stringify(status),{headers:{'Set-Cookie':`fc666_admin_session="${token}"; Path=/`}});
  assert.equal((await GET(request('status',{method:'GET'}),context('status'))).headers.get('set-cookie'),null,'quoted nonempty tokens remain unsupported');
  globalThis.fetch=async()=>new Response(JSON.stringify(status),{headers:{'Set-Cookie':'unrelated=secret; Path=/'}});
  const response=await GET(request('status',{method:'GET'}),context('status'));assert.equal(response.headers.get('set-cookie'),null);
 }finally{globalThis.fetch=oldFetch;if(oldBase===undefined)delete process.env.DIRECTIONAL_API_BASE_URL;else process.env.DIRECTIONAL_API_BASE_URL=oldBase;}
});
