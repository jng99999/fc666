"""Actual saved history, lost acknowledgement retry, refresh and offline export."""
import json
import subprocess
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import ROOT
from scripts.verify_portfolio_history import verify
from tests.browser_terminal import ARGS

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    root='http://127.0.0.1:3000';errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':393,'height':852},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        # Use a real existing stopped Paper account. Never create fake market data.
        accounts=page.request.get(root+'/api/paper/streams?limit=20&status=STOPPED').json()['items']
        assert accounts,'A real stopped account is required'
        id=accounts[0]['session_id']
        page.goto(root+'/portfolio',wait_until='domcontentloaded')
        page.evaluate('(id)=>localStorage.setItem("fc666.portfolio.selection.v1",JSON.stringify([id]))',id)
        page.goto(root+'/portfolio/history',wait_until='domcontentloaded')
        name='Browser immutable '+str(__import__('uuid').uuid4())
        page.get_by_label('场景名称',exact=True).fill(name)
        # The server commits successfully but this browser loses its acknowledgement.
        committed={}
        def lose(route):
            if route.request.method!='POST':route.continue_();return
            response=route.fetch();assert response.status==200,response.text()
            committed['scenario']=response.json();route.abort()
        page.route('**/api/portfolio/scenarios',lose)
        page.get_by_role('button',name='冻结并保存场景',exact=True).click()
        expect(page.get_by_role('button',name='重试原请求',exact=True)).to_be_enabled()
        page.unroute('**/api/portfolio/scenarios',lose)
        page.reload(wait_until='domcontentloaded')
        page.get_by_role('button',name='重试原请求',exact=True).click()
        expect(page.get_by_role('heading',name=name,exact=True)).to_be_visible()
        scenario=committed['scenario'];assert scenario['definition']['session_ids']==[id]
        captured={}
        def lose_snapshot(route):
            if route.request.method!='POST':route.continue_();return
            response=route.fetch();assert response.status==200,response.text()
            captured['value']=response.json();route.abort()
        pattern='**/api/portfolio/scenarios/'+scenario['scenario_id']+'/snapshots'
        page.route(pattern,lose_snapshot)
        page.get_by_role('button',name='保存当前估值快照',exact=True).click()
        expect(page.get_by_role('button',name='重试原请求',exact=True)).to_be_enabled()
        page.unroute(pattern,lose_snapshot)
        page.get_by_role('button',name='重试原请求',exact=True).click()
        expect(page.get_by_test_id('history-detail')).to_be_visible()
        with page.expect_download() as download:page.get_by_role('button',name='下载历史快照',exact=True).click()
        path=ROOT/'.runtime/browser-portfolio-history.json';download.value.save_as(str(path))
        value=json.loads(path.read_text());assert value==captured['value'];verify(value)
        listing=page.request.get(root+'/api/portfolio/scenarios/'+scenario['scenario_id']+'/snapshots').json()
        assert len(listing['items'])==1
        page.reload(wait_until='domcontentloaded')
        page.get_by_role('button',name=name,exact=True).click()
        page.get_by_role('button',name='查看快照',exact=True).click()
        expect(page.get_by_test_id('history-detail')).to_contain_text(value['report_sha256'])
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        page.screenshot(path=str(ROOT/'.runtime/portfolio-history-mobile.png'),full_page=True)
        print('PASS: real Paper account; scenario and snapshot lost-ack retries are idempotent; refresh; exact offline export; 393px layout; no JS errors')
        browser.close()
