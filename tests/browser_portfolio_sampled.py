"""Real saved samples, separate chart paths, exact metrics/export and reload failure."""
import json
from copy import deepcopy
from core.backtest.spot import digest
from core.portfolio.valuation import evaluate
import subprocess
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import ROOT
from core.portfolio import sampled
from tests.browser_terminal import ARGS

if __name__=='__main__':
    root='http://127.0.0.1:3000';errors=[]
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':393,'height':852},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        scenes=page.request.get(root+'/api/portfolio/scenarios?limit=10').json()['items']
        choice=None
        for scene in scenes:
            response=page.request.get(root+'/api/portfolio/scenarios/'+scene['scenario_id']+'/sampled?limit=8')
            assert response.status==200,response.text()
            value=response.json()
            if len(value['points'])>=2:choice=(scene,value);break
        assert choice,'Requires a real saved scenario with multiple snapshots'
        scene,original=choice;id=scene['scenario_id'];sampled.verify(original)
        page.goto(root+'/portfolio/history',wait_until='domcontentloaded')
        page.get_by_role('button',name=scene['definition']['name'],exact=True).click()
        expect(page.get_by_test_id('sampled-result')).to_have_count(0)
        with page.expect_response(lambda response:'/sampled?' in response.url) as response:
            page.get_by_role('button',name='查看采样权益',exact=True).click()
        assert response.value.status==200,response.value.text()
        report=response.value.json();assert report==original
        result=page.get_by_test_id('sampled-result');expect(result).to_be_visible()
        expected_segments=[segment['segment_id'] for segment in report['segments'] if segment['observations']>1]
        assert result.locator('polyline').evaluate_all('(items)=>items.map(item=>item.dataset.segment)')==expected_segments
        expect(result.locator('circle')).to_have_count(sum(point['equity'] is not None for point in report['points']))
        expect(result.locator('[data-unavailable]')).to_have_count(sum(point['equity'] is None for point in report['points']))
        for segment in report['segments']:
            if segment['sampled']['max_drawdown_amount'] is not None:expect(result).to_contain_text(segment['sampled']['max_drawdown_amount'])
        if result.locator('circle').count():
            result.locator('circle').first.focus()
            expect(result).to_contain_text('选中观察：')
            first=next(point for point in report['points'] if point['equity'] is not None)
            expect(result).to_contain_text(f"权益 {first['equity']} USDT")
        with page.expect_download() as download:page.get_by_role('button',name='下载采样权益分析',exact=True).click()
        path=ROOT/'.runtime/browser-portfolio-sampled.json';download.value.save_as(str(path))
        assert json.loads(path.read_text())==report;sampled.verify(json.loads(path.read_text()))
        assert page.request.get(root+'/api/portfolio/scenarios/'+id+'/sampled?limit=8').json()==original
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/portfolio-sampled-mobile.png'),full_page=True)
        pattern='**/api/portfolio/scenarios/'+id+'/sampled?*'
        # Exercise missing-value plot rendering only in this browser; never alter storage.
        degraded_inputs=deepcopy(report['inputs'])
        damaged=degraded_inputs['snapshots'][1]
        for account in damaged['report']['inputs']['accounts']:account['quote']=None
        damaged['report']=evaluate(damaged['report']['inputs']);damaged['report_sha256']=digest(damaged['report'])
        degraded=sampled.evaluate(degraded_inputs)
        def unavailable(route):route.fulfill(status=200,json=degraded)
        page.route(pattern,unavailable)
        page.get_by_role('button',name='查看采样权益',exact=True).click()
        expect(result.locator('[data-unavailable]')).to_have_count(sum(point['equity'] is None for point in degraded['points']))
        expect(result).to_contain_text('不可汇总')
        expected_paths=[segment['segment_id'] for segment in degraded['segments'] if segment['observations']>1]
        assert result.locator('polyline').evaluate_all('(items)=>items.map(item=>item.dataset.segment)')==expected_paths
        page.unroute(pattern,unavailable)
        page.route(pattern,lambda route:route.fulfill(status=409,json={'detail':'Stored input not verifiable'}))
        page.get_by_role('button',name='查看采样权益',exact=True).click()
        expect(result).to_have_count(0)
        expect(page.get_by_test_id('portfolio-sampled').get_by_role('alert')).to_contain_text('采样权益分析不可用 (409)')
        page.unroute(pattern)
        page.reload(wait_until='domcontentloaded')
        page.get_by_role('button',name=scene['definition']['name'],exact=True).click()
        expect(result).to_have_count(0)
        assert not errors,errors
        print('PASS: real immutable observations; separate segment chart paths; exact point focus and metrics; offline export; read-only repeatability; failed reload clears result; 393px; no JS errors')
        browser.close()
