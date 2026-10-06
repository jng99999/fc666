"""Real public market valuation, loss of quotes, selection restoration and exact export."""
import json
import subprocess
import time
from copy import deepcopy
from playwright.sync_api import sync_playwright, expect
from scripts.dev_services import ROOT
from scripts.verify_portfolio import verify
from tests.browser_terminal import ARGS


if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    errors=[];checks=[];ids=[]
    root='http://127.0.0.1:3000'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            for symbol in ['BTCUSDT','ETHUSDT']:
                response=page.request.post(root+'/api/paper/streams',data={'symbol':symbol,'timeframe':'1m','limit':8,'strategy':'ema_long_flat_v1','parameters':{'period':2},'config':{'period':2,'initial_cash':'10000'}})
                assert response.status==201,response.text()
                id=response.json()['session_id'];ids.append(id)
                for _ in range(3):
                    state=page.request.get(root+'/api/paper/streams/'+id).json()
                    response=page.request.post(root+'/api/paper/streams/'+id+'/command',data={'expected_revision':state['revision'],'action':'stop'})
                    if response.status==200:break
                    assert response.status==409
                assert response.status==200
            before=[page.request.get(root+'/api/paper/streams/'+id).json() for id in ids]
            page.goto(root+'/portfolio',wait_until='domcontentloaded')
            expect(page.get_by_role('button',name='计算当前模拟估值',exact=True)).to_be_disabled()
            for id in ids:page.get_by_label('选择账户 '+id,exact=True).check()
            page.get_by_label('总资产敞口阈值',exact=True).fill('0.7')
            page.get_by_label('单币种敞口阈值',exact=True).fill('0.4')
            result=page.get_by_test_id('portfolio-result')
            deadline=time.monotonic()+20
            while True:
                with page.expect_response(lambda response:response.url.endswith('/api/portfolio') and response.request.method=='POST') as response:
                    page.get_by_role('button',name='计算当前模拟估值',exact=True).click()
                assert response.value.status==200,response.value.text()
                report=response.value.json();verify(report)
                if report['status']=='COMPLETE':break
                if time.monotonic()>deadline:raise AssertionError(report['alerts'])
                page.wait_for_timeout(1000)
            expect(result).to_have_attribute('data-status','COMPLETE')
            assert len(report['rows'])==2 and {row['symbol'] for row in report['rows']}=={'BTCUSDT','ETHUSDT'}
            assert all(row['quote']['close_time']==report['rows'][0]['quote']['close_time'] for row in report['rows'])
            assert report['inputs']['limits']=={'max_gross_weight':'0.7','max_asset_weight':'0.4'}
            assert [page.request.get(root+'/api/paper/streams/'+id).json() for id in ids]==before
            with page.expect_download() as download:page.get_by_role('button',name='下载本次估值快照',exact=True).click()
            path=ROOT/'.runtime/browser-portfolio.json';download.value.save_as(str(path))
            assert json.loads(path.read_text())==report;verify(report)
            checks.append('explicit BTC/ETH realtime accounts use common real closed-minute prices; valuation does not mutate stopped accounts; raw export reproduces exact totals offline')
            page.reload(wait_until='domcontentloaded')
            for id in ids:expect(page.get_by_label('选择账户 '+id,exact=True)).to_be_checked()
            expect(result).to_have_count(0)
            assert page.evaluate("JSON.parse(localStorage.getItem('fc666.portfolio.selection.v1'))")==ids
            # Inject a degraded HTTP response only in this test browser; no product/DB market data is modified.
            degraded=deepcopy(report);degraded.update(status='UNAVAILABLE',totals=None,exposures=[])
            degraded['rows'][0].update(reason='QUOTE_STALE',equity=None,market_value=None)
            degraded['alerts']=[{'code':'QUOTE_STALE','session_id':ids[0]}]
            def unavailable(route):route.fulfill(status=200,json=degraded)
            page.route('**/api/portfolio',unavailable)
            page.get_by_role('button',name='计算当前模拟估值',exact=True).click()
            expect(result).to_have_attribute('data-status','UNAVAILABLE')
            expect(result).to_contain_text('公共分钟报价陈旧')
            expect(result).to_contain_text('不把缺失值当零')
            expect(result.locator('.research-metrics')).to_have_count(0)
            page.unroute('**/api/portfolio',unavailable)
            page.get_by_label('单币种敞口阈值',exact=True).fill('1.5')
            page.get_by_role('button',name='计算当前模拟估值',exact=True).click()
            expect(page.locator('main section > [role=alert]')).to_contain_text('估值请求失败')
            expect(result).to_have_count(0)
            page.get_by_label('单币种敞口阈值',exact=True).fill('0.4')
            page.get_by_role('button',name='计算当前模拟估值',exact=True).click()
            expect(result).to_have_attribute('data-status','COMPLETE')
            page.set_viewport_size({'width':393,'height':852})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'.runtime/portfolio-mobile.png'),full_page=True)
            checks.append('refresh restores selection without displaying old valuation; missing/stale values suppress aggregate metrics; invalid thresholds clear prior result; mobile fits')
            page.get_by_role('button',name='清空选择',exact=True).click()
            expect(result).to_have_count(0)
            expect(page.get_by_role('button',name='计算当前模拟估值',exact=True)).to_be_disabled()
            assert not errors,errors
        finally:
            for id in ids:
                for _ in range(3):
                    state=page.request.get(root+'/api/paper/streams/'+id).json()
                    if state.get('status')=='STOPPED':break
                    response=page.request.post(root+'/api/paper/streams/'+id+'/command',data={'expected_revision':state['revision'],'action':'stop'})
                    if response.status==200:break
                    assert response.status==409
            browser.close()
    output={'passed':checks,'browser_errors':errors,'session_ids':ids,'valuation_status':report['status']}
    (ROOT/'.runtime/portfolio-browser-report.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
