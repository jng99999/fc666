// Invoke this function with a Playwright Page against the local production build.
// All directional API responses are explicit fixtures; no model or order service is called.
async (page) => {
 const checks=[];
 const check=(condition,label)=>{if(!condition)throw new Error(label);checks.push(label);};
 const id='12345678-1234-4234-8234-123456789012',id2='22345678-1234-4234-8234-123456789013',jobId='32345678-1234-4234-8234-123456789014';
 const at='2026-11-02T10:00:00Z';
 const config={instrument:'XAU/USD',quote_source:'swissquote.test-only',initial_cash:'10000',tick_size:'0.01',quantity_step:'0.001',max_quantity:'10',max_order_quote:'5000',fee_rate:'0.001',slippage:'0.05',news_pre_seconds:1800,news_post_seconds:1800};
 const account={account_id:id,revision:8,status:'RUNNING',created_at:at,updated_at:at,mode:'RESEARCH_PAPER',trading_enabled:false,config,metadata:{strategy:'AI_RESEARCH'},balance:'10000',equity:'10000',fees:'0',gross_pnl:'0',unrealized_pnl:'0',entry_halted:false,position:null,pending:null,fills:[],fills_count:0,fills_truncated:false,events:1,journal_sha256:null,last_quote:null,entry_gate:'NEWS_UNAVAILABLE',recent_quotes:[],last_outcome:null};
 const account2={...account,account_id:id2,config:{...config,instrument:'BTCUSDT',quote_source:'bitget.test-only'}};
 const status={framework:'TradingAgents',upstream_commit:'c4544913a14ed3df5f1ed7b9e8483270ce20682a',runner_configured:false,model_configured:false,automation_supported:true,state:'WAIT_MODEL_CONFIG',mode:'SHADOW_ONLY',automatic_execution:false,trading_enabled:false};
 let control={account_id:id,enabled:false,epoch:0,interval_seconds:300,next_due:null,last_job:null,state:'PAUSED',receipt:null,trading_enabled:false};
 let fault='',writes=[],creates=[],jobs=[];
 const seen=new Map();
 const snapshot={sha256:'a'.repeat(64),snapshot:{version:'fc666-research-snapshot-v1',account_id:id,revision:8,at,valid_until:'2026-11-02T10:05:00Z',config,quote:null,recent_quotes:[],sample_type:'OBSERVED_QUOTES_NOT_CANDLES',entry_gate:'NEWS_UNAVAILABLE',unavailable:['closed_candles','ohlcv_indicators','social_sentiment','company_fundamentals'],market_evidence:{status:'UNAVAILABLE',reason:'HISTORICAL_CANDLES_UNAVAILABLE',bars:[],indicators:null,quote_source:config.quote_source,candle_source:null},trading_enabled:false}};
 const prepared={job_id:jobId,account_id:id,mode:'PREPARE',state:'SNAPSHOT_ONLY',snapshot,result:null,created_at:at,finished_at:at,trading_enabled:false};
 async function mock(route){
  const request=route.request(),path=new URL(request.url()).pathname.replace('/api/directional/','');
  const send=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(path==='status')return send({service:'fixture-only',mode:'RESEARCH_PAPER',trading_enabled:false,news:{configured:false,state:'NEWS_UNAVAILABLE'},ai:{configured:false},feed:{},instruments:[{instrument:'XAU/USD',quote_source:config.quote_source,currency:'USD',unit:'oz'},{instrument:'BTCUSDT',quote_source:account2.config.quote_source,currency:'USDT',unit:'BTC'}]});
  if(path==='accounts'&&request.method()==='POST'){creates.push(request.postDataJSON());return send(account,201);}
  if(path==='accounts')return send({accounts:[account,account2],trading_enabled:false});
  if(path==='accounts/'+id)return send(account);
  if(path==='accounts/'+id2)return send(account2);
  if(path==='research/status')return send(status);
  if(path==='research/jobs')return send({...status,jobs,authorized_accounts:[]});
  if(path==='research/jobs/'+jobId)return send(jobs.find(row=>row.job_id===jobId)??prepared);
  if(path==='research/accounts/'+id){jobs=[prepared];return send(prepared,201);}
  if(path==='research/automation/'+id2)return send({...control,account_id:id2,enabled:false,state:'PAUSED',last_job:null,receipt:null});
  if(path==='research/automation/'+id){
   if(request.method()==='GET')return send(control);
   const body=request.postDataJSON();writes.push(body);
   if(seen.has(body.request_id))return send(seen.get(body.request_id));
   if(fault==='conflict'){fault='';control={...control,epoch:control.epoch+1};return send({detail:'Control changed'},409);}
   control={...control,epoch:control.epoch+1,enabled:body.action==='START',state:body.action==='START'?'WAIT_MODEL_CONFIG':'PAUSED',interval_seconds:body.interval_seconds};
   seen.set(body.request_id,{...control});
   if(fault==='uncertain'){fault='';return send({detail:'Result uncertain'},502);}
   return send(control);
  }
  throw new Error('Unmocked directional route '+request.method()+' '+path);
 }
 const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/directional/**',mock);
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('http://127.0.0.1:3069/research/ai?account='+id);
 const panel=page.getByTestId('ai-automation');
 await panel.getByText('未启用自动模拟 · 自动模拟已暂停',{exact:true}).waitFor();
 const start=panel.getByRole('button',{name:'启用自动模拟',exact:true}),pause=panel.getByRole('button',{name:'暂停自动模拟',exact:true});
 check(await page.getByRole('button',{name:'运行 AI 研究',exact:true}).isDisabled(),'unconfigured single-model calls stay disabled');
 const interval=panel.getByRole('spinbutton');
 await interval.fill('299');check(await start.isDisabled(),'interval below 300 seconds is rejected');
 await interval.fill('300');
 await start.evaluate(button=>{button.click();button.click();});
 await panel.getByText('已启用自动模拟意愿 · 等待服务器配置模型，未调用模型',{exact:true}).waitFor();
 check(writes.length===1&&writes[0].action==='START','double-click sends one START command');
 check(writes[0].expected_epoch===0&&writes[0].expected_revision===8,'START binds the current epoch and server account revision');
 await pause.click();await panel.getByText('未启用自动模拟 · 自动模拟已暂停',{exact:true}).waitFor();
 check(writes.length===2&&writes[1].action==='PAUSE','PAUSE remains a distinct reversible control');
 fault='uncertain';await start.click();await panel.getByRole('button',{name:'重查同一自动模拟请求'}).waitFor();
 check(await pause.isDisabled(),'uncertain control blocks a conflicting new command');
 await panel.getByRole('button',{name:'重查同一自动模拟请求'}).click();
 await panel.getByText('已启用自动模拟意愿 · 等待服务器配置模型，未调用模型',{exact:true}).waitFor();
 check(writes.length===4&&writes[2].request_id===writes[3].request_id,'uncertain response retries the same request identifier');
 await pause.click();await panel.getByText('未启用自动模拟 · 自动模拟已暂停',{exact:true}).waitFor();
 fault='conflict';await start.click();await panel.getByRole('alert').filter({hasText:'账户或控制版本已变化'}).waitFor();
 const epoch=control.epoch;
 check((await panel.innerText()).includes('控制版本 '+epoch),'epoch conflict refreshes the latest control state');
 await start.click();await panel.getByText('已启用自动模拟意愿 · 等待服务器配置模型，未调用模型',{exact:true}).waitFor();
 check(writes.at(-1).expected_epoch===epoch,'fresh confirmation uses the refreshed epoch');
 control={...control,state:'HISTORY_UNAVAILABLE',receipt:{job_id:jobId,request_id:'ta:auto:'+jobId,status:'ACCEPTED',reason:'ACCEPTED',at,account_revision:9,fills_count:0}};
 await panel.getByRole('button',{name:'刷新自动模拟状态'}).click();
 await panel.getByText('已启用自动模拟意愿 · 黄金历史行情尚未接入，暂停新开仓',{exact:true}).waitFor();
 check((await panel.innerText()).includes('接收不代表成交'),'ACCEPTED receipt clearly waits for later quotes');
 control={...control,receipt:{...control.receipt,status:'FILLED',reason:'SIGNAL_ENTRY',fills_count:1}};
 await panel.getByRole('button',{name:'刷新自动模拟状态'}).click();await panel.getByText('已模拟成交',{exact:true}).waitFor();
 checks.push('receipt advances from ACCEPTED to FILLED without treating acceptance as a fill');
 await page.getByRole('button',{name:'保存研究快照',exact:true}).click();
 await page.getByTestId('ai-research-job').getByText('黄金历史行情尚未接入，暂停新开仓',{exact:true}).waitFor();
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'下载当前研究记录'}).click();
 check((await download).suggestedFilename()==='fc666-research-'+jobId+'.json','snapshot download preserves its research identifier');
 const bars=Array.from({length:100},(_,index)=>({open_time:index*60000,close_time:(index+1)*60000,open:'100',high:'102',low:'99',close:'101',volume:'1'}));
 const market={status:'READY',reason:'CLOSED_CANDLES_READY',bars,indicators:{ema20:'100.5',rsi14:'50',atr14:'3',as_of:6000000,input_bars:100,method:'test-fixture-only'},quote_source:'bitget.test-only',candle_source:'bitget.test-only:1min'};
 const roles=['bull','bear','research_manager','trader','aggressive','conservative','neutral','portfolio_manager'];
 jobs=[{...prepared,mode:'AUTO',state:'COMPLETED',snapshot:{...snapshot,snapshot:{...snapshot.snapshot,market_evidence:market}},result:{advice:{adapter:'fc666-tradingagents-shadow-v1',upstream_commit:status.upstream_commit,snapshot_sha256:snapshot.sha256,instrument:config.instrument,quote_source:config.quote_source,produced_at:at,valid_until:'2026-11-02T10:05:00Z',rating:'Hold',reports:roles.map(role=>({role,text:'Fixture-only report: '+role})),candidate:{target:'HOLD',quantity:'0',stop_loss:null,take_profit:null,reason:'Fixture-only insufficient evidence'},model:'fixture-only-no-api',model_calls:9},assessment:{reason:'HOLD',validation_passed:true,execution_enabled:false,trading_enabled:false,checked_at:at}}}];
 control={...control,last_job:jobId};await panel.getByRole('button',{name:'刷新自动模拟状态'}).click();
 await panel.getByRole('button',{name:'查看最近模型建议'}).click();
 await page.getByTestId('ai-research-job').getByText('研究评级：Hold',{exact:true}).waitFor();
 check((await page.getByTestId('ai-research-job').innerText()).includes('已接入 100 根独立历史闭合柱'),'ready historical evidence replaces the missing-history statement');
 check(await page.getByText('Fixture-only report: bull',{exact:true}).count()===1,'model role reports render from the received research record');
 await page.setViewportSize({width:320,height:740});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'320px viewport has no horizontal page overflow');
 await panel.getByRole('button',{name:'暂停自动模拟'}).scrollIntoViewIfNeeded();
 const box=await panel.getByRole('button',{name:'暂停自动模拟'}).boundingBox();
 check(box&&box.x>=0&&box.x+box.width<=320,'mobile pause control stays within the viewport');
 const second=await page.context().newPage();
 try{
  await second.route('**/api/directional/**',mock);
  await second.goto('http://127.0.0.1:3069/research/ai');
  await second.getByRole('button',{name:'AI研究账户',exact:true}).click();
  await second.getByRole('option').filter({hasText:id2.slice(-8)}).click();
  await second.getByTestId('ai-automation').getByText('未启用自动模拟 · 自动模拟已暂停',{exact:true}).waitFor();
  check((await page.getByRole('button',{name:'AI研究账户',exact:true}).innerText()).includes(id.slice(-8)),'a second tab keeps its account choice independent');
  await second.goto('http://127.0.0.1:3069/paper/directional');
  await second.getByRole('button',{name:'双向模拟决策方式',exact:true}).click();
  await second.getByRole('option',{name:'AI 研究自动模拟',exact:true}).click();
  await second.getByRole('button',{name:'创建独立模拟账户',exact:true}).click();
  await second.getByTestId('directional-account').waitFor();
  check(creates.length===1&&creates[0].strategy==='AI_RESEARCH','account form creates the real AI_RESEARCH strategy');
  check(!['quantity','stop_loss_percent','take_profit_percent'].some(key=>key in creates[0]),'AI account creation omits fixed position and protection parameters');
  check(!((await second.locator('main').innerText()).includes('AI 服务尚未配置')),'directional page links to actual model configuration instead of a fixed unavailable label');
 }finally{await second.close();}
 check(errors.length===0,'no browser page errors during the mocked control flow');
 return {fixture_only:true,checks:checks.length,passed:checks,model_calls:0,private_orders:0};
}
