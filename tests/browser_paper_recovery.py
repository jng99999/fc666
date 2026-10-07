"""Real retained simulated fill receipts, read-only inspection and exact offline export."""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import ROOT
from core.paper.recovery import verify
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
        print('PASS: real stopped account with fills; stable receipts; coherent offline export; no account mutation; failed inspection clears confirmation; refresh retains account; 393px; no JS errors')
        browser.close()
