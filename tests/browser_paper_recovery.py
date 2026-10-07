"""Real retained simulated fill receipts, read-only inspection and exact offline export."""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import ROOT
from core.paper.recovery import verify
from core.paper import intents
from tests.browser_terminal import ARGS

if __name__=='__main__':
    root='http://127.0.0.1:3000';errors=[]
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':393,'height':852},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        candidates=[];retained=ROOT/'.runtime/portfolio-retained-position.json'
        if retained.exists():candidates.extend(json.loads(retained.read_text())['inputs']['selected_ids'])
        cursor=None
        for _ in range(10):
            listing=page.request.get(root+'/api/paper/streams',params={'limit':20,'status':'STOPPED',**({'cursor':cursor} if cursor else {})}).json()
            candidates.extend(item['session_id'] for item in listing['items'])
            cursor=listing['next_cursor']
            if not cursor:break
        chosen=None
        for id in dict.fromkeys(candidates):
            response=page.request.get(root+'/api/paper/streams/'+id)
            if response.status==200:
                value=response.json()
                if value['status']=='STOPPED' and value['fills']:chosen=value;break
        assert chosen,'Requires a real retained stopped account with simulated fills'
        id=chosen['session_id']
        page.goto(root+'/paper/realtime',wait_until='domcontentloaded')
        page.evaluate('(id)=>localStorage.setItem("fc666.paper.stream.v1",id)',id)
        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_test_id('paper-stream-state')).to_have_attribute('data-status','STOPPED')
        page.wait_for_timeout(4500)
        expect(page.get_by_test_id('paper-recovery')).to_have_count(1)
        with page.expect_response(lambda response:response.url.endswith('/recovery')) as response:
            page.get_by_role('button',name='检查持久执行状态',exact=True).click()
        assert response.value.status==200,response.value.text()
        report=response.value.json();verify(report)
        result=page.get_by_test_id('recovery-result');expect(result).to_be_visible()
        assert report['account_status']=='STOPPED' and report['automatic_replay'] is False
        assert [receipt['order']['order_id'] for receipt in report['receipts']]==[order['order_id'] for order in chosen['orders']]
        expect(result).to_contain_text(chosen['fills'][0]['order_id'])
        with page.expect_download() as download:page.get_by_role('button',name='下载执行恢复检查',exact=True).click()
        path=ROOT/'.runtime/browser-paper-recovery.json';download.value.save_as(str(path))
        assert json.loads(path.read_text())==report;verify(json.loads(path.read_text()))
        expect(page.get_by_test_id('paper-intents')).to_have_count(1)
        with page.expect_response(lambda response:response.url.endswith('/intents')) as intent_response:
            page.get_by_role('button',name='核对独立模拟订单',exact=True).click()
        assert intent_response.value.status==200,intent_response.value.text()
        intent_report=intent_response.value.json();intents.verify(intent_report)
        intent_result=page.get_by_test_id('intent-result');expect(intent_result).to_be_visible()
        assert intent_report['ledger_orders']==len(chosen['orders'])
        expect(intent_result).to_have_attribute('data-coverage',intent_report['coverage'])
        with page.expect_download() as intent_download:page.get_by_role('button',name='下载独立模拟订单',exact=True).click()
        intent_path=ROOT/'.runtime/browser-paper-intents.json';intent_download.value.save_as(str(intent_path))
        assert json.loads(intent_path.read_text())==intent_report;intents.verify(json.loads(intent_path.read_text()))
        intent_pattern='**/api/paper/streams/'+id+'/intents'
        page.route(intent_pattern,lambda route:route.fulfill(status=409,json={'detail':'Intent mismatch'}))
        page.get_by_role('button',name='核对独立模拟订单',exact=True).click()
        expect(intent_result).to_have_count(0)
        expect(page.get_by_test_id('paper-intents').get_by_role('alert')).to_contain_text('订单意图核对未确认 (409)')
        page.unroute(intent_pattern)
        expect(page.get_by_test_id('paper-preparations')).to_have_count(1)
        with page.expect_response(lambda response:response.url.endswith('/preparations')) as prep_response:
            page.get_by_role('button',name='读取模拟准备记录',exact=True).click()
        assert prep_response.value.status==200,prep_response.value.text()
        prep_report=prep_response.value.json();assert prep_report['session_id']==id
        assert prep_report['trading_enabled'] is prep_report['external_submission_supported'] is False
        prep_result=page.get_by_test_id('preparation-result');expect(prep_result).to_be_visible()
        assert len(prep_report['records'])<=1000
        prep_pattern='**/api/paper/streams/'+id+'/preparations'
        page.route(prep_pattern,lambda route:route.fulfill(status=409,json={'detail':'Preparation mismatch'}))
        page.get_by_role('button',name='读取模拟准备记录',exact=True).click()
        expect(prep_result).to_have_count(0)
        expect(page.get_by_test_id('paper-preparations').get_by_role('alert')).to_contain_text('准备记录读取未确认 (409)')
        page.unroute(prep_pattern)
        expect(page.get_by_test_id('paper-authorizations')).to_have_count(1)
        with page.expect_response(lambda response:response.url.endswith('/authorizations')) as auth_response:
            page.get_by_role('button',name='核对逐单模拟授权',exact=True).click()
        assert auth_response.value.status==200,auth_response.value.text()
        auth_report=auth_response.value.json();assert auth_report['session_id']==id
        assert auth_report['trading_enabled'] is auth_report['external_submission_supported'] is False
        assert auth_report['account_gate']['buy_allowed'] is auth_report['account_gate']['sell_allowed'] is False
        assert auth_report['window_limit']==20 and len(auth_report['batches'])<=20
        auth_result=page.get_by_test_id('authorization-result');expect(auth_result).to_be_visible()
        expect(auth_result).to_contain_text('当前账户 STOPPED')
        auth_pattern='**/api/paper/streams/'+id+'/authorizations'
        page.route(auth_pattern,lambda route:route.fulfill(status=409,json={'detail':'Authorization mismatch'}))
        page.get_by_role('button',name='核对逐单模拟授权',exact=True).click()
        expect(auth_result).to_have_count(0)
        expect(page.get_by_test_id('paper-authorizations').get_by_role('alert')).to_contain_text('逐单授权核对未确认 (409)')
        page.unroute(auth_pattern)
        expect(page.get_by_test_id('paper-lifecycle')).to_have_count(1)
        with page.expect_response(lambda response:response.url.endswith('/lifecycle')) as life_response:
            page.get_by_role('button',name='核对模拟生命周期',exact=True).click()
        assert life_response.value.status==200,life_response.value.text()
        life_report=life_response.value.json();assert life_report['session_id']==id
        assert life_report['trading_enabled'] is life_report['external_reconciliation_supported'] is False
        assert life_report['window_limit']==20 and len(life_report['batches'])<=20
        life_result=page.get_by_test_id('lifecycle-result');expect(life_result).to_be_visible()
        life_pattern='**/api/paper/streams/'+id+'/lifecycle'
        page.route(life_pattern,lambda route:route.fulfill(status=409,json={'detail':'Lifecycle mismatch'}))
        page.get_by_role('button',name='核对模拟生命周期',exact=True).click()
        expect(life_result).to_have_count(0)
        expect(page.get_by_test_id('paper-lifecycle').get_by_role('alert')).to_contain_text('生命周期核对未确认 (409)')
        page.unroute(life_pattern)
        expect(page.get_by_test_id('paper-funding')).to_have_count(1)
        with page.expect_response(lambda response:response.url.endswith('/funding')) as funding_response:
            page.get_by_role('button',name='核对模拟资金预留',exact=True).click()
        assert funding_response.value.status==200,funding_response.value.text()
        funding_report=funding_response.value.json();assert funding_report['session_id']==id
        assert funding_report['trading_enabled'] is funding_report['external_submission_supported'] is False
        assert funding_report['window_limit']==20 and len(funding_report['batches'])<=20
        funding_result=page.get_by_test_id('funding-result');expect(funding_result).to_be_visible()
        funding_pattern='**/api/paper/streams/'+id+'/funding'
        page.route(funding_pattern,lambda route:route.fulfill(status=409,json={'detail':'Funding mismatch'}))
        page.get_by_role('button',name='核对模拟资金预留',exact=True).click()
        expect(funding_result).to_have_count(0)
        expect(page.get_by_test_id('paper-funding').get_by_role('alert')).to_contain_text('资金预留核对未确认 (409)')
        page.unroute(funding_pattern)
        assert page.request.get(root+'/api/paper/streams/'+id).json()==chosen
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/paper-recovery-mobile.png'),full_page=True)
        pattern='**/api/paper/streams/'+id+'/recovery'
        page.route(pattern,lambda route:route.fulfill(status=409,json={'detail':'Ledger cannot be verified'}))
        page.get_by_role('button',name='检查持久执行状态',exact=True).click()
        expect(result).to_have_count(0)
        expect(page.get_by_test_id('paper-recovery').get_by_role('alert')).to_contain_text('恢复检查未确认 (409)')
        page.unroute(pattern)
        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_test_id('paper-stream-state')).to_have_attribute('data-status','STOPPED')
        expect(result).to_have_count(0)
        assert not errors,errors
        print('PASS: real stopped account with fills; stable receipts; independent intent coverage and offline export; preparation, authorization, lifecycle and funding history; stopped gates and failure clearing; coherent recovery export; no account mutation; failed inspection clears confirmation; refresh retains account; 393px; no JS errors')
        browser.close()
