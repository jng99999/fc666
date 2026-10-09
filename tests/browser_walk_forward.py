"""Actual stored-market-data task, worker, refresh and export acceptance."""
import json,subprocess
from playwright.sync_api import sync_playwright,expect
from tests.browser_terminal import ARGS
from core.backtest.walk_forward import verify

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],text=True).strip();errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
            page.locator('select').nth(2).select_option('1m')
            page.get_by_label('历史柱数').fill('120');page.get_by_label('EMA 周期',exact=True).fill('2')
            panel=page.get_by_test_id('walk-forward-research')
            panel.get_by_label('滚动训练柱数').fill('40');panel.get_by_label('滚动测试柱数').fill('20')
            panel.get_by_label('滚动候选参数').fill('[{"period":2},{"period":3}]')
            with page.expect_response(lambda r:r.url.endswith('/api/research/walk-forwards') and r.request.method=='POST') as response:
                panel.get_by_role('button',name='运行滚动研究',exact=True).click()
            assert response.value.status==202,response.value.text()
            expect(panel.get_by_test_id('walk-forward-task')).to_have_attribute('data-status','SUCCEEDED',timeout=30000)
            expect(panel.get_by_test_id('walk-forward-result')).to_be_visible()
            page.reload(wait_until='domcontentloaded');panel=page.get_by_test_id('walk-forward-research')
            expect(panel.get_by_test_id('walk-forward-result')).to_be_visible(timeout=30000)
            with page.expect_download() as download:panel.get_by_role('button',name='下载完整滚动结果').click()
            report=json.load(open(download.value.path()));assert verify(report)==report
            assert len(report['folds'])==4 and len(report['dataset'])==120 and not errors,errors
            print('Walk-forward browser acceptance passed: real task, worker, refresh, full export replay; 4 folds')
        finally:browser.close()
