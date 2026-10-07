"""Immutable real risk observations, policy export and browser-only degraded response."""
import json
import subprocess
from copy import deepcopy
from playwright.sync_api import sync_playwright,expect
from core.backtest.spot import digest
from core.portfolio import risk_history
from core.portfolio.valuation import evaluate
from scripts.dev_services import ROOT
from tests.browser_terminal import ARGS

if __name__=='__main__':
    root='http://127.0.0.1:3000';errors=[]
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':393,'height':852},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        scenes=page.request.get(root+'/api/portfolio/scenarios?limit=10').json()['items'];choice=None
        for scene in scenes:
            response=page.request.get(root+'/api/portfolio/scenarios/'+scene['scenario_id']+'/risk?limit=8')
            assert response.status==200,response.text()
            report=response.json()
            if len(report['points'])>=2:choice=(scene,report);break
        assert choice,'Requires real saved multi-point scenario'
        scene,original=choice;id=scene['scenario_id'];risk_history.verify(original)
        page.goto(root+'/portfolio/history',wait_until='domcontentloaded')
        page.get_by_role('button',name=scene['definition']['name'],exact=True).click()
        expect(page.get_by_test_id('risk-result')).to_have_count(0)
        with page.expect_response(lambda response:'/risk?' in response.url) as response:
            page.get_by_role('button',name='查看风险提示历史',exact=True).click()
        assert response.value.status==200,response.value.text()
        result=page.get_by_test_id('risk-result');expect(result).to_be_visible()
        assert response.value.json()==original
        expect(result).to_contain_text(original['policy_sha256'])
        expect(result.get_by_test_id('risk-point')).to_have_count(len(original['points']))
        with page.expect_download() as download:page.get_by_role('button',name='下载风险提示历史',exact=True).click()
        path=ROOT/'.runtime/browser-portfolio-risk.json';download.value.save_as(str(path))
        assert json.loads(path.read_text())==original;risk_history.verify(json.loads(path.read_text()))
        assert page.request.get(root+'/api/portfolio/scenarios/'+id+'/risk?limit=8').json()==original
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/portfolio-risk-mobile.png'),full_page=True)
        # Only this browser receives a missing quote; product data/records stay untouched.
        source=deepcopy(original['inputs']);damaged=source['snapshots'][1]
        for account in damaged['report']['inputs']['accounts']:account['quote']=None
        damaged['report']=evaluate(damaged['report']['inputs']);damaged['report_sha256']=digest(damaged['report'])
        degraded=risk_history.evaluate(source)
        pattern='**/api/portfolio/scenarios/'+id+'/risk?*'
        def unavailable(route):route.fulfill(status=200,json=degraded)
        page.route(pattern,unavailable)
        page.get_by_role('button',name='查看风险提示历史',exact=True).click()
        unknown=result.locator('[data-status="UNKNOWN"]');expect(unknown).to_have_count(degraded['summary']['unknown'])
        expect(unknown.first).to_contain_text('状态未确认')
        expect(unknown.first).to_contain_text('不可计算')
        page.unroute(pattern,unavailable)
        page.route(pattern,lambda route:route.fulfill(status=409,json={'detail':'Cannot verify history'}))
        page.get_by_role('button',name='查看风险提示历史',exact=True).click()
        expect(result).to_have_count(0)
        expect(page.get_by_test_id('portfolio-risk').get_by_role('alert')).to_contain_text('风险历史不可用 (409)')
        page.unroute(pattern)
        page.reload(wait_until='domcontentloaded')
        page.get_by_role('button',name=scene['definition']['name'],exact=True).click()
        expect(result).to_have_count(0)
        assert not errors,errors
        print('PASS: real saved risk observations; frozen policy and exact offline export; no storage mutation; missing quote is UNKNOWN; failed refresh clears stale metrics; 393px; no JS errors')
        browser.close()
