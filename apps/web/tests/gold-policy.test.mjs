import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {buildSync} from 'esbuild';
import {GET,POST} from '../app/api/gold/[...segments]/route.ts';
import {GoldAccount,GoldRequestError,goldRequest} from '../lib/gold.ts';

const output=buildSync({entryPoints:[fileURLToPath(new URL('../lib/gold-policy.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'});
const compiled={exports:{}};
new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),compiled,compiled.exports);
const {PolicyAccount,PolicyDetail,PolicyList,PolicyStatus,PolicyCreate,PolicyCommand,PendingPolicy,PolicyRequestReceipt,verifyPolicyReceipt}=compiled.exports;
const id='95cac022-c9dc-49c8-97ca-130b934b2429';
const requestId='3ce35cab-06af-46b4-9d3c-0648d95aecf4';
const checkedAt=Date.UTC(2026,9,22,3);
const config={initial_cash:'10000',allocation:'.5',max_spread:'2',commission:'.10',slippage:'.05'};
const model={id:'ppo-validated-fixture',protocol:'gold-ppo-target-v1',feature_version:'closed-hour-v1',valid_until:checkedAt+3600000,account_config:config};
const context=segments=>({params:Promise.resolve({segments})});
const url=segments=>'https://test.example/api/gold/'+segments.join('/');
const post=(segments,body={},headers={})=>POST(new Request(url(segments),{method:'POST',headers:{origin:'https://test.example','content-type':'application/json',...headers},body:typeof body==='string'?body:JSON.stringify(body)}),context(segments));

function status(overrides={}){
 return {enabled:true,trading_enabled:false,source:'swissquote.public:AT:AT:standard:XAU/USD',required_bars:100,complete_bars:100,latest_close_at:checkedAt,warmup_status:'READY',status:'READY',models:[structuredClone(model)],checked_at:checkedAt,...overrides};
}
function account(overrides={}){
 return {id,revision:7,status:'ACTIVE',mode:'POLICY',cash:'7500',quantity:'1.25',basis:'2500',fees:'.125',realized:'0',currency:'USD',instrument:'XAU/USD',trading_enabled:false,phase:'WAITING_FORWARD_HOUR',created_at:checkedAt-10000,updated_at:checkedAt,last_bar:null,
  config:{...config,mode:'POLICY',policy:{model_id:model.id,actor_sha256:'a'.repeat(64),context_hash:'b'.repeat(64),protocol:model.protocol,feature_version:model.feature_version}},...overrides};
}
const canonical=value=>JSON.stringify((function sort(input){return Array.isArray(input)?input.map(sort):input&&typeof input==='object'?Object.fromEntries(Object.keys(input).sort().map(key=>[key,sort(input[key])])):input;})(value));
const digest=value=>createHash('sha256').update(canonical(value),'utf8').digest('hex');
function pendingCommand(action='DECIDE'){
 return PendingPolicy.parse({kind:'command',account_id:id,body:{request_id:requestId,expected_revision:7,action},uncertain:true});
}
function commandReceipt(action='DECIDE'){
 const pending=pendingCommand(action);
 return {found:true,request_id:requestId,account_id:id,seq:8,kind:`POLICY_${action}`,event_hash:'c'.repeat(64),request_hash:digest(pending.body),hash_scope:'COMMAND_BODY',state:account({revision:8}),details:{reason:'EXCESSIVE_SPREAD'},account:account({revision:10,status:'PAUSED'})};
}
function pendingCreate(){
 return PendingPolicy.parse({kind:'create',body:{...config,request_id:requestId,mode:'POLICY',model_id:model.id,experimental:true}});
}
function createReceipt(){
 const state=account({revision:1});
 return {found:true,request_id:requestId,account_id:id,seq:1,kind:'CREATED',event_hash:'d'.repeat(64),request_hash:digest(state.config),hash_scope:'ACCOUNT_CONFIG',state,details:{source:'swissquote.public:AT:AT:standard:XAU/USD'},account:account({revision:10,status:'STOPPED'})};
}

test('policy status accepts disabled, no models, incomplete history, stale hour and ready states',()=>{
 const fixtures=[
  status({enabled:false,status:'DISABLED',models:[],complete_bars:0,latest_close_at:null,warmup_status:'WAITING_HISTORY'}),
  status({status:'NO_QUALIFIED_MODELS',models:[],complete_bars:0,latest_close_at:null,warmup_status:'WAITING_HISTORY'}),
  status({status:'WAITING_HISTORY',complete_bars:73,latest_close_at:checkedAt-3600000,warmup_status:'WAITING_HISTORY'}),
  status({status:'WAITING_FRESH_CLOSED_HOUR',warmup_status:'WAITING_FRESH_CLOSED_HOUR',latest_close_at:checkedAt-7200000}),
  status(),
 ];
 for(const fixture of fixtures)assert.deepEqual(PolicyStatus.parse(fixture),fixture);
});

test('policy ready status rejects inconsistent readiness or execution flags',()=>{
 for(const change of [
  {models:[]},{complete_bars:99},{latest_close_at:null},{warmup_status:'WAITING_HISTORY'},
  {enabled:false},{trading_enabled:true},{status:'DISABLED'},
  {status:'NO_QUALIFIED_MODELS'},{complete_bars:101},{checked_at:NaN},
 ])assert.equal(PolicyStatus.safeParse(status(change)).success,false,JSON.stringify(change));
 const malformed=status();malformed.models[0].account_config.commission='NaN';
 assert.equal(PolicyStatus.safeParse(malformed).success,false);
});

test('policy account schemas preserve independent mode and cannot masquerade as manual or SMA20',()=>{
 const policy=account();
 assert.deepEqual(PolicyAccount.parse(policy),policy);
 assert.equal(GoldAccount.safeParse(policy).success,false);
 for(const mode of ['MANUAL','SMA20']){
  const regular={...policy,mode,intent:null,config};
  assert.equal(GoldAccount.safeParse(regular).success,true);
  assert.equal(PolicyAccount.safeParse(regular).success,false);
  assert.equal(PolicyList.safeParse({accounts:[regular],enabled:true,trading_enabled:false}).success,false);
 }
 assert.equal(PolicyAccount.safeParse(account({trading_enabled:true})).success,false);
 for(const value of ['NaN','Infinity','-Infinity','', '0x10'])assert.equal(PolicyAccount.safeParse(account({cash:value})).success,false,value);
 assert.equal(PolicyAccount.safeParse(account({config:{...policy.config,mode:'SMA20'}})).success,false);
 assert.equal(PolicyList.safeParse({accounts:[policy],enabled:false,trading_enabled:true}).success,false);
 assert.equal(PolicyAccount.parse(account({status:'STOPPED'})).status,'STOPPED');
 const detail={...policy,events:[{seq:1,at:checkedAt,hash:'c'.repeat(64),kind:'POLICY_SKIPPED',details:{reason:'WAITING_HISTORY'}}],next_before:null};
 assert.deepEqual(PolicyDetail.parse(detail),detail);
 assert.equal(PolicyDetail.safeParse({...detail,next_before:-1}).success,false);
});

test('pending requests round trip the same UUID, account and expected revision without inventing retries',()=>{
 const create={kind:'create',body:{...config,request_id:requestId,mode:'POLICY',model_id:model.id,experimental:true}};
 const command={kind:'command',account_id:id,body:{request_id:requestId,expected_revision:7,action:'STOP'}};
 for(const pending of [create,command]){
  const restored=PendingPolicy.parse(JSON.parse(JSON.stringify(pending)));
  assert.deepEqual(restored,{...pending,uncertain:true});
  assert.equal(restored.body.request_id,requestId);
  const fresh=PendingPolicy.parse({...pending,uncertain:false});
  assert.equal(fresh.uncertain,false);assert.deepEqual(fresh.body,restored.body);
  const unknown=PendingPolicy.parse(JSON.parse(JSON.stringify({...restored,uncertain:true})));
  assert.equal(unknown.uncertain,true);
  assert.equal(unknown.body.request_id,restored.body.request_id);
  assert.deepEqual(unknown.body,restored.body);
 }
 assert.equal(PendingPolicy.parse(command).body.expected_revision,7);
 for(const action of ['DECIDE','PAUSE','RESUME','STOP'])assert.equal(PolicyCommand.parse({...command.body,action}).action,action);
 for(const body of [{...command.body,expected_revision:0},{...command.body,expected_revision:NaN},{...command.body,request_id:'new-id'},{...command.body,action:'BUY'}])assert.equal(PolicyCommand.safeParse(body).success,false);
 assert.equal(PolicyCreate.safeParse({...create.body,experimental:false}).success,false);
 assert.equal(PolicyCreate.safeParse({...create.body,mode:'MANUAL'}).success,false);
 assert.equal(PendingPolicy.safeParse({...command,account_id:'bad-account'}).success,false);
 assert.equal(PendingPolicy.safeParse({...command,uncertain:'false'}).success,false);
});

test('policy proxy permits exactly bounded status, list, detail, create and command resources',async t=>{
 const sent=[];
 t.mock.method(globalThis,'fetch',async(target,options)=>{sent.push({target:String(target),options});return Response.json({trading_enabled:false});});
 for(const segments of [['policy','status'],['policy','accounts'],['policy','accounts',id]])assert.equal((await GET(new Request(url(segments)),context(segments))).status,200);
 assert.equal((await post(['policy','accounts'])).status,200);
 assert.equal((await post(['policy','accounts',id,'command'])).status,200);
 assert.equal(sent.length,5);
 assert.ok(sent.every(row=>new URL(row.target).pathname.startsWith('/api/v1/gold/policy/')));
 for(const segments of [
  ['policy'],['policy','models'],['policy','accounts','bad'],['policy','accounts',id,'command'],
  ['policy','accounts',id,'events'],['policy','accounts',id,'private'],['policy','status','extra'],
 ])assert.equal((await GET(new Request(url(segments)),context(segments))).status,404,segments.join('/'));
 for(const segments of [['policy','status'],['policy','accounts',id],['policy','accounts',id,'events'],['policy','models'],['policy','accounts',id,'command','extra']])assert.equal((await post(segments)).status,404,segments.join('/'));
 assert.equal(sent.length,5);
});

test('policy commands reject cross origin or cross site before contacting the backend',async t=>{
 let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({});});
 for(const headers of [{origin:'https://attacker.example'},{origin:'http://test.example'},{origin:'null'},{origin:'https://test.example','sec-fetch-site':'cross-site'}])assert.equal((await post(['policy','accounts'],{},headers)).status,403);
 const noOrigin=new Request(url(['policy','accounts']),{method:'POST',headers:{'sec-fetch-site':'cross-site'},body:'{}'});
 assert.equal((await POST(noOrigin,context(['policy','accounts']))).status,403);
 assert.equal(calls,0);
});

test('policy proxy forwards only the isolated gold cookie and preserves immutable request bodies',async t=>{
 const sent=[];
 t.mock.method(globalThis,'fetch',async(target,options)=>{sent.push({target:String(target),options});return Response.json({trading_enabled:false},{headers:{'Set-Cookie':`fc666_gold_session=${'y'.repeat(43)}; HttpOnly; Secure; Path=/api`}});});
 const body={request_id:requestId,expected_revision:7,action:'DECIDE'};
 const response=await post(['policy','accounts',id,'command'],body,{cookie:`session=private-other; fc666_gold_session=${'x'.repeat(43)}; csrf=private-csrf`,authorization:'Bearer do-not-forward'});
 assert.equal(response.status,200);
 assert.deepEqual(sent[0].options.headers,{'Content-Type':'application/json',Cookie:`fc666_gold_session=${'x'.repeat(43)}`});
 assert.deepEqual(JSON.parse(sent[0].options.body),body);
 assert.equal(sent[0].options.method,'POST');assert.equal(sent[0].options.cache,'no-store');
 assert.equal(response.headers.get('cache-control'),'no-store');assert.ok(response.headers.get('set-cookie').includes('HttpOnly'));
 await post(['policy','accounts'],{}, {cookie:`session=private; fc666_gold_session=invalid`});
 assert.equal(sent[1].options.headers.Cookie,undefined);
});

test('policy POST failures are never replayed and an explicit retry retains UUID and revision',async t=>{
 t.mock.method(console,'warn',()=>{});
 const sent=[];
 t.mock.method(globalThis,'fetch',async(target,options)=>{sent.push({target:String(target),body:options.body});throw new TypeError('connection reset');});
 const pending=PendingPolicy.parse({kind:'command',account_id:id,body:{request_id:requestId,expected_revision:7,action:'STOP'}});
 const segments=['policy','accounts',pending.account_id,'command'];
 assert.equal((await post(segments,pending.body)).status,502);assert.equal(sent.length,1);
 const restored=PendingPolicy.parse(JSON.parse(JSON.stringify(pending)));
 assert.equal((await post(segments,restored.body)).status,502);assert.equal(sent.length,2);
 assert.deepEqual(sent[0],sent[1]);
});

test('policy POST upstream error and oversized body do not produce duplicate backend writes',async t=>{
 let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({detail:'temporarily unavailable'},{status:503});});
 assert.equal((await post(['policy','accounts'],{})).status,503);assert.equal(calls,1);
 assert.equal((await post(['policy','accounts'],'x'.repeat(8193))).status,413);assert.equal(calls,1);
});

test('policy client surfaces version conflicts without automatically issuing another request',async t=>{
 let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({detail:'Expected revision is stale; refresh account'},{status:409});});
 await assert.rejects(goldRequest(`policy/accounts/${id}/command`,undefined,{request_id:requestId,expected_revision:7,action:'DECIDE'}),error=>{
  assert.ok(error instanceof GoldRequestError);assert.equal(error.name,'GoldRequestError');
  assert.equal(error.status,409);assert.equal(error.message,'Expected revision is stale; refresh account');return true;
 });
 assert.equal(calls,1);
});

test('pending state distinguishes a new definite rejection from a previously unknown outcome',async t=>{
 const first=PendingPolicy.parse({kind:'command',account_id:id,body:{request_id:requestId,expected_revision:7,action:'DECIDE'},uncertain:false});
 const unknown=PendingPolicy.parse(JSON.parse(JSON.stringify({...first,uncertain:true})));
 assert.equal(first.uncertain,false);assert.equal(unknown.uncertain,true);
 let calls=0;
 t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({detail:'Revision conflict'},{status:409});});
 for(const pending of [first,unknown]){
  await assert.rejects(goldRequest(`policy/accounts/${id}/command`,undefined,pending.body),error=>{
   assert.ok(error instanceof GoldRequestError);assert.equal(error.status,409);return true;
  });
 }
 assert.equal(calls,2);
 assert.equal(unknown.uncertain,true);
 assert.equal(unknown.body.expected_revision,7);assert.equal(unknown.body.request_id,requestId);
 assert.deepEqual(unknown.body,first.body);
});

test('gold errors retain HTTP status while transport failures remain unknown outcomes',async t=>{
 let calls=0;
 t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({detail:'Unavailable'},{status:408});});
 await assert.rejects(goldRequest('policy/accounts',undefined,{}),error=>{assert.ok(error instanceof GoldRequestError);assert.equal(error.status,408);return true;});
 t.mock.method(globalThis,'fetch',async()=>{calls++;throw new TypeError('network unavailable');});
 await assert.rejects(goldRequest('policy/accounts',undefined,{}),error=>{assert.ok(error instanceof TypeError);assert.equal(error instanceof GoldRequestError,false);assert.equal(error.status,undefined);return true;});
 assert.equal(calls,2);
});

test('missing immutable receipt preserves unknown request and never proves it was not applied',async()=>{
 const pending=pendingCommand();
 const missing={found:false,request_id:requestId,absence_proves_not_applied:false};
 const result=await verifyPolicyReceipt(pending,missing);
 assert.deepEqual(result,missing);assert.equal(pending.uncertain,true);
 assert.equal(pending.body.request_id,requestId);assert.equal(pending.body.expected_revision,7);
 assert.equal(PolicyRequestReceipt.safeParse({...missing,absence_proves_not_applied:true}).success,false);
 await assert.rejects(verifyPolicyReceipt(pending,{...missing,request_id:id}),/编号不一致/);
});

test('found immutable command receipt matches original body, not current account revision or status',async()=>{
 for(const action of ['DECIDE','PAUSE','RESUME','STOP']){
  const receipt=commandReceipt(action);
  const verified=await verifyPolicyReceipt(pendingCommand(action),receipt);
  assert.deepEqual(verified,receipt);
  assert.equal(verified.seq,8);assert.equal(verified.account.revision,10);
 }
 const refusal=await verifyPolicyReceipt(pendingCommand(),commandReceipt());
 assert.equal(refusal.details.reason,'EXCESSIVE_SPREAD');
 assert.equal(refusal.details.fill,undefined);
});

test('immutable command receipt rejects UUID, account, kind, hash scope, body hash or revision mismatch',async()=>{
 const receipt=commandReceipt();
 const mutations=[
  {...receipt,request_id:id},
  {...receipt,account_id:requestId},
  {...receipt,state:account({id:requestId,revision:8})},
  {...receipt,account:account({id:requestId,revision:10})},
  {...receipt,kind:'POLICY_STOP'},
  {...receipt,hash_scope:'ACCOUNT_CONFIG'},
  {...receipt,request_hash:'0'.repeat(64)},
  {...receipt,seq:9},
  {...receipt,state:account({revision:9})},
  {...receipt,account:account({revision:7})},
 ];
 for(const invalid of mutations)await assert.rejects(verifyPolicyReceipt(pendingCommand(),invalid),/不一致/);
 for(const key of ['request_hash','event_hash'])assert.equal(PolicyRequestReceipt.safeParse({...receipt,[key]:'invalid-hash'}).success,false);
 const changedBody=pendingCommand();changedBody.body.expected_revision=6;
 await assert.rejects(verifyPolicyReceipt(changedBody,receipt),/不一致/);
});

test('legacy create pending reconciles against original config and exact exponent decimal identities',async()=>{
 const pending=pendingCreate();assert.equal(pending.uncertain,true);
 const receipt=createReceipt();
 Object.assign(receipt.state.config,{initial_cash:'1.0000e4',allocation:'5E-1',max_spread:'2.00e+0',commission:'1e-1',slippage:'5.00e-2'});
 receipt.request_hash=digest(receipt.state.config);
 const verified=await verifyPolicyReceipt(pending,receipt);
 assert.equal(verified.found,true);assert.equal(verified.account.status,'STOPPED');
 assert.equal(verified.state.revision,1);assert.equal(verified.state.config.initial_cash,'1.0000e4');
 assert.equal(pending.body.request_id,requestId);
 const approximate=createReceipt();approximate.state.config.initial_cash='10000.0000000000001';
 approximate.request_hash=digest(approximate.state.config);
 assert.equal(Number(approximate.state.config.initial_cash),Number(pending.body.initial_cash));
 await assert.rejects(verifyPolicyReceipt(pending,approximate),/配置不一致/);
});

test('create receipt rejects changed model, money, kind or hash scope',async()=>{
 const pending=pendingCreate();const receipt=createReceipt();
 const wrongModel=structuredClone(receipt);wrongModel.state.config.policy.model_id='another-model';wrongModel.request_hash=digest(wrongModel.state.config);
 const wrongMoney=structuredClone(receipt);wrongMoney.state.config.commission='.11';wrongMoney.request_hash=digest(wrongMoney.state.config);
 for(const invalid of [wrongModel,wrongMoney,{...receipt,kind:'POLICY_DECIDE'},{...receipt,hash_scope:'COMMAND_BODY'}])await assert.rejects(verifyPolicyReceipt(pending,invalid),/不一致/);
});

test('create receipt rejects a valid-looking but mismatched original configuration hash',async()=>{
 await assert.rejects(verifyPolicyReceipt(pendingCreate(),{...createReceipt(),request_hash:'0'.repeat(64)}),/不一致/);
});

test('CREATED receipt cannot claim a later revision even if state and receipt agree',async()=>{
 const laterCreation=createReceipt();laterCreation.seq=2;laterCreation.state.revision=2;
 await assert.rejects(verifyPolicyReceipt(pendingCreate(),laterCreation),/不一致/);
});

test('receipt lookup proxy is read only, bounded and forwards only the gold owner cookie',async t=>{
 const sent=[];const missing={found:false,request_id:requestId,absence_proves_not_applied:false};
 t.mock.method(globalThis,'fetch',async(target,options)=>{sent.push({target:String(target),options});return Response.json(missing);});
 const segments=['policy','requests',requestId];
 const response=await GET(new Request(url(segments),{headers:{cookie:`other=private; fc666_gold_session=${'x'.repeat(43)}`}}),context(segments));
 assert.equal(response.status,200);assert.deepEqual(await response.json(),missing);
 assert.equal(sent[0].options.method,'GET');assert.equal(sent[0].options.body,undefined);
 assert.equal(sent[0].options.headers.Cookie,`fc666_gold_session=${'x'.repeat(43)}`);
 assert.equal(new URL(sent[0].target).pathname,`/api/v1/gold/policy/requests/${requestId}`);
 for(const path of [['policy','requests'],['policy','requests','invalid'],['policy','requests',requestId,'command'],['policy','requests',requestId,'extra']])assert.equal((await GET(new Request(url(path)),context(path))).status,404);
 assert.equal((await post(segments)).status,404);
 assert.equal((await post(['policy','requests'])).status,404);
 assert.equal(sent.length,1);
});

test('receipt lookup conflict or unauthorized result never converts to a missing receipt or writes',async t=>{
 let calls=0;
 t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({detail:'Request ID matches multiple policy accounts; inspect account histories'},{status:409});});
 const segments=['policy','requests',requestId];
 const response=await GET(new Request(url(segments)),context(segments));
 assert.equal(response.status,409);assert.match((await response.json()).detail,/multiple policy accounts/);
 assert.equal(calls,1);
 t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({detail:'Gold session required'},{status:401});});
 assert.equal((await GET(new Request(url(segments)),context(segments))).status,401);
 assert.equal(calls,2);
});
