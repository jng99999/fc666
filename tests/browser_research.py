"""Real data end-to-end indicator panes and reproducible research exports."""
import json
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright,expect
from tests.browser_terminal import ARGS
from scripts.reproduce_backtest import reproduce

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN',accept_downloads=True)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:3000',wait_until='domcontentloaded')
        page.wait_for_function("document.querySelector('.chart-caption').textContent.match(/[1-9][0-9]+ 根/)")
        canvas_count=page.get_by_test_id('price-chart').locator('canvas').count()
        with page.expect_response(lambda r:'/api/market/indicators' in r.url) as response:page.get_by_role('button',name='指标 overlays').click()
        assert response.value.status==200
        page.wait_for_function(f"document.querySelectorAll('[data-testid=price-chart] canvas').length>{canvas_count}")
        expect(page.locator('.chart-caption')).to_contain_text('RSI14 / MACD / ATR14')
        with page.expect_response(lambda r:'/api/market/indicators' in r.url and 'period=50' in r.url) as response:page.get_by_label('均线周期',exact=True).select_option('50')
        assert response.value.json()['period']==50
        expect(page.locator('.chart-caption')).to_contain_text('SMA50')
        with page.expect_response(lambda r:'/api/market/indicators' in r.url and 'limit=120' in r.url) as response:page.get_by_label('历史窗口',exact=True).select_option('120')
        assert len(response.value.json()['rows'])==120
        assert response.value.json()['rows'][-1]['regime']['rule_version']=='er-atr-v1'
        expect(page.get_by_test_id('market-regime')).not_to_have_text('')
        page.screenshot(path=str(ROOT/'.runtime/terminal-indicators.png'),full_page=True)
        page.get_by_role('button',name='指标 overlays').click()
        page.wait_for_function(f"document.querySelectorAll('[data-testid=price-chart] canvas').length=={canvas_count}")
        checks.append('RSI/MACD/ATR pane creation/removal and live parameter/window query paths')
        page.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
        with page.expect_response(lambda r:'/api/market/backtest' in r.url) as response:page.get_by_role('button',name='运行历史回测').click()
        assert response.value.status==200
        data=response.value.json();run_id=reproduce(data)
        assert data['manifest']['engine_version']=='spot-next-open-v2'
        assert 'analysis' in data and 'market_states' in data
        expect(page.get_by_test_id('backtest-result')).to_contain_text('已完成')
        expect(page.locator('.research-metrics>div')).to_have_count(9)
        assert 'NaN' not in page.locator('.research-metrics').inner_text()
        expect(page.locator('.research-metrics')).to_contain_text('%')
        expect(page.get_by_role('img',name='回测权益曲线')).to_be_visible()
        expect(page.get_by_role('img',name='回测回撤曲线')).to_be_visible()
        expect(page.get_by_test_id('risk-analysis')).to_contain_text('Sharpe')
        assert 'NaN' not in page.get_by_test_id('risk-analysis').inner_text()
        page.get_by_label('截止时间（含时区，可空）').fill(data['manifest']['end'])
        with page.expect_response(lambda r:'/api/market/backtest' in r.url) as response:page.get_by_role('button',name='运行历史回测').click()
        assert response.value.json()['run_id']==run_id
        with page.expect_download() as download:page.get_by_role('button',name='下载结果与清单').click()
        exported=ROOT/'.runtime/browser-backtest-export.json';download.value.save_as(str(exported))
        assert reproduce(json.loads(exported.read_text()))==run_id
        page.screenshot(path=str(ROOT/'.runtime/research-desktop.png'),full_page=True)
        checks.append('real historical run, fixed-cutoff determinism, downloaded full export offline reproduction')
        page.get_by_label('费率',exact=True).fill('0.5');page.get_by_role('button',name='运行历史回测').click()
        expect(page.locator('.research-page p[role=alert]')).to_contain_text('参数或历史数据不可用')
        expect(page.get_by_test_id('backtest-result')).to_have_count(0)
        checks.append('invalid input clears prior success and reports rejection')
        phone=context.new_page();phone.set_viewport_size({'width':393,'height':852});phone.on('pageerror',lambda e:errors.append(str(e)))
        phone.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
        assert phone.evaluate('document.documentElement.scrollWidth<=innerWidth')
        phone.screenshot(path=str(ROOT/'.runtime/research-mobile.png'),full_page=True)
        assert not errors,errors
        browser.close()
    result={'passed':checks,'run_id':run_id,'browser_errors':errors}
    (ROOT/'.runtime/research-browser-report.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
