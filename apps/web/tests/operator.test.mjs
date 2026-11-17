import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const output=await build({entryPoints:[fileURLToPath(new URL('../lib/operator.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'});
const module={exports:{}};new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
const {OperatorStatus,OperatorConfiguration,draftFromConfig,configurationPayload,modelProviderSelection,selectModelProvider}=module.exports;
const config={revision:4,model:{provider:'openai',model:'actual-model-id',base_url:'https://models.example/v1',api_key_configured:true},news:{source:'actual-calendar',url:'https://calendar.example/events',token_configured:true},gold:{source:'actual-spot',url:'https://gold.example/history',token_configured:true},authorized_accounts:['12345678-1234-4234-8234-123456789012'],trading_enabled:false};

test('operator schemas reject plaintext credentials and live-enabled responses',()=>{
 assert.equal(OperatorConfiguration.safeParse(config).success,true);
 for(const model of [{...config.model,api_key:'MUST_NOT_ENTER_BROWSER'},{...config.model,token:'MUST_NOT_ENTER_BROWSER'}])assert.equal(OperatorConfiguration.safeParse({...config,model}).success,false);
 assert.equal(OperatorConfiguration.safeParse({...config,trading_enabled:true}).success,false);
 const session={initialized:true,authenticated:true,username:'operator',csrf_token:'a'.repeat(64),config_revision:4,trading_enabled:false};
 assert.equal(OperatorStatus.safeParse(session).success,true);
 assert.equal(OperatorStatus.safeParse({...session,password:'not permitted'}).success,false);
 assert.equal(OperatorStatus.safeParse({...session,trading_enabled:true}).success,false);
});

test('drafts never read back credentials; empty secrets preserve them and explicit flags clear them',()=>{
 const draft=draftFromConfig(OperatorConfiguration.parse(config));
 assert.equal(draft.api_key,'');assert.equal(draft.news_token,'');assert.equal(draft.gold_token,'');
 const body=configurationPayload(draft,4,config);
 assert.equal(body.expected_revision,4);
 assert(!('api_key'in body.model));assert(!('token'in body.news));assert(!('token'in body.gold));
 assert.deepEqual(body.authorized_accounts,config.authorized_accounts);
 const clear=configurationPayload({...draft,clear_api_key:true,clear_news_token:true,clear_gold_token:true},4,config);
 assert.equal(clear.model.clear_api_key,true);assert.equal(clear.news.clear_token,true);assert.equal(clear.gold.clear_token,true);
});

test('changing credential destination requires a replacement or explicit removal',()=>{
 const draft=draftFromConfig(config);
 assert.throws(()=>configurationPayload({...draft,provider:'anthropic'},4,config),/重新填写密钥/);
 assert.throws(()=>configurationPayload({...draft,base_url:'https://other.example/v1'},4,config),/重新填写密钥/);
 assert.throws(()=>configurationPayload({...draft,news_url:'https://other.example/news'},4,config),/新闻接口/);
 assert.throws(()=>configurationPayload({...draft,gold_url:'https://other.example/gold'},4,config),/黄金接口/);
 assert.equal(configurationPayload({...draft,provider:'anthropic',api_key:'REPLACEMENT_TEST_KEY'},4,config).model.api_key,'REPLACEMENT_TEST_KEY');
 assert.equal(configurationPayload({...draft,provider:'anthropic',clear_api_key:true},4,config).model.clear_api_key,true);
});

test('configuration validates endpoint semantics, provider identity and bounded owned-account identifiers',()=>{
 const draft=draftFromConfig(config);
 for(const base_url of ['http://models.example/v1','https://user:pass@models.example/v1','https://models.example/v1?key=hidden','https://models.example/v1#secret'])assert.throws(()=>configurationPayload({...draft,base_url},4),/HTTPS/);
 assert.throws(()=>configurationPayload({...draft,provider:'google'},4),/Google/);
 assert.throws(()=>configurationPayload({...draft,provider:'',model:'filled'},4),/同时填写/);
 assert.throws(()=>configurationPayload({...draft,news_url:'',news_source:'filled'},4),/同时填写/);
 assert.throws(()=>configurationPayload({...draft,accounts:'not-a-uuid'},4));
 assert.throws(()=>configurationPayload({...draft,accounts:Array(21).fill(config.authorized_accounts[0]).join('\n')},4));
 assert.throws(()=>configurationPayload({...draft,accounts:config.authorized_accounts[0]+'\n'+config.authorized_accounts[0]},4),/重复/);
 assert.throws(()=>configurationPayload({...draft,api_key:'line1\nline2'},4),/密钥/);
});

test('DeepSeek selection uses the official OpenAI-compatible destination without carrying another provider credential or model',()=>{
 const old={...draftFromConfig(config),api_key:'UNSAVED_OLD_PROVIDER_KEY'};
 const draft=selectModelProvider(old,'deepseek');
 assert.equal(modelProviderSelection(draft),'deepseek');assert.equal(draft.provider,'openai');
 assert.equal(draft.base_url,'https://api.deepseek.com');assert.equal(draft.api_key,'');assert.equal(draft.model,'');
 assert.throws(()=>configurationPayload({...draft,model:'deepseek-flash'},4,config),/重新填写密钥/);
 const ready={...draft,model:'deepseek-flash',api_key:'EXPLICIT_DEEPSEEK_FIXTURE_KEY'};
 const body=configurationPayload(ready,4,config);
 assert.deepEqual(body.model,{provider:'openai',model:'deepseek-flash',base_url:'https://api.deepseek.com',api_key:'EXPLICIT_DEEPSEEK_FIXTURE_KEY',clear_api_key:false});
 assert.deepEqual(selectModelProvider(ready,'deepseek'),ready,'reselecting the same destination keeps this unsaved form');
});

test('saved DeepSeek configurations restore the visible provider but never restore a key, and leaving the preset clears its destination',()=>{
 for(const base_url of ['https://api.deepseek.com','https://api.deepseek.com/','https://api.deepseek.com/v1','https://api.deepseek.com/v1/']){
  const saved={...config,model:{provider:'openai',model:'deepseek-flash',base_url,api_key_configured:true}};
  const draft=draftFromConfig(OperatorConfiguration.parse(saved));assert.equal(modelProviderSelection(draft),'deepseek');assert.equal(draft.api_key,'');
 }
 const draft={...draftFromConfig(config),provider:'openai',model:'deepseek-flash',base_url:'https://api.deepseek.com',api_key:'UNSAVED_DEEPSEEK_KEY'};
 const other=selectModelProvider(draft,'openai');assert.equal(modelProviderSelection(other),'openai');assert.equal(other.base_url,'');assert.equal(other.api_key,'');assert.equal(other.model,'');
});

test('DeepSeek identity is determined by the exact HTTPS endpoint, not the model name or a similar hostname',()=>{
 for(const base_url of ['https://api.deepseek.com.evil.example','https://other.example/deepseek','http://api.deepseek.com','https://user@api.deepseek.com','https://api.deepseek.com?key=hidden','https://api.deepseek.com#fragment']){
  assert.equal(modelProviderSelection({provider:'openai',base_url}),'openai');
 }
 assert.equal(modelProviderSelection({provider:'anthropic',base_url:'https://api.deepseek.com'}),'anthropic');
 assert.throws(()=>selectModelProvider(draftFromConfig(config),'unknown-provider'));
});
