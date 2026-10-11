"""Actual light terminal, mobile overflow and unavailable-market acceptance."""
import subprocess
from playwright.sync_api import sync_playwright, expect
from tests.browser_terminal import ARGS


if __name__ == '__main__':
    executable=subprocess.check_output(['node','scripts/browser_path.mjs'],text=True).strip()
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,args=ARGS)
        page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:3000',wait_until='domcontentloaded')
        expect(page.get_by_test_id('live-price')).not_to_have_text('—',timeout=30000)
        page.wait_for_function("/[1-9][0-9]+ 根/.test(document.querySelector('.chart-caption').textContent)")
        assert page.evaluate("getComputedStyle(document.documentElement).colorScheme")=='light'
        page.screenshot(path='.runtime/terminal-light-desktop.png',full_page=True)
        page.get_by_role('tab',name='策略',exact=True).click()
        expect(page.get_by_role('link',name='打开研究工作台')).to_have_attribute('href','/research')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth')<=390
        page.screenshot(path='.runtime/terminal-light-mobile.png',full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.goto('http://127.0.0.1:3000/research',wait_until='networkidle')
        expect(page.get_by_role('button',name='运行历史回测',exact=True)).to_be_visible()
        page.screenshot(path='.runtime/research-light.png',full_page=True)
        # Explicit failure injection verifies the empty/error treatment, not data.
        offline=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
        offline.on('pageerror',lambda e:errors.append(str(e)))
        offline.route('**/api/market/candles?**',lambda route:route.fulfill(status=503,json={'error':'fixture unavailable'}))
        offline.goto('http://127.0.0.1:3000',wait_until='domcontentloaded')
        alert=offline.locator('.chart-body').get_by_role('alert')
        expect(alert).to_contain_text('行情服务暂不可用',timeout=15000)
        expect(alert.get_by_role('link',name='查看服务状态')).to_have_attribute('href','/system')
        offline.screenshot(path='.runtime/terminal-light-unavailable.png',full_page=True)
        assert not errors,errors
        browser.close()
    print('Light workstation passed: actual data, mobile layout, research entry and explicit failure state')
