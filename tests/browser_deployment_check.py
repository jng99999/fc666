"""Real public-data diagnostics plus explicit transport failure injection."""
import subprocess
from playwright.sync_api import sync_playwright, expect
from tests.browser_terminal import ARGS

if __name__=='__main__':
    executable=subprocess.check_output(['node','scripts/browser_path.mjs'],text=True).strip()
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,args=ARGS)
        page=browser.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:3000/system',wait_until='domcontentloaded')
        for name in ['api','history','socket']:
            expect(page.get_by_test_id('deployment-'+name)).to_have_text('本次检查通过',timeout=15000)
        page.get_by_role('button',name='重新检查连接').click()
        expect(page.get_by_test_id('deployment-socket')).to_have_text('本次检查通过',timeout=15000)
        page.close()
        # A subscription acknowledgement alone must never pass live-data acceptance.
        page=browser.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route_web_socket('**/stream/v1/market',lambda ws:ws.on_message(lambda _:ws.send('{"type":"subscribed"}')))
        page.goto('http://127.0.0.1:3000/system',wait_until='domcontentloaded')
        expect(page.get_by_test_id('deployment-socket')).to_have_text('未通过',timeout=12000)
        expect(page.get_by_role('region',name='站点连接检查').get_by_role('alert')).to_contain_text('连接检查未全部通过')
        expect(page.get_by_test_id('deployment-api')).to_have_text('本次检查通过')
        page.close()
        # Even structurally valid historical/stale live snapshots must fail.
        import httpx
        snapshot=httpx.get('http://127.0.0.1:3000/api/market/snapshot?symbol=BTCUSDT&channel=ticker').json()
        snapshot['received_at']='2000-01-01T00:00:00Z'
        import json
        page=browser.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route_web_socket('**/stream/v1/market',lambda ws:ws.on_message(lambda _:ws.send(json.dumps(snapshot))))
        page.route('**/api/health',lambda route:route.fulfill(status=502,json={'error':'unavailable'}))
        page.route('**/api/market/candles?**',lambda route:route.fulfill(json=[]))
        page.goto('http://127.0.0.1:3000/system',wait_until='domcontentloaded')
        for name in ['api','history','socket']:
            expect(page.get_by_test_id('deployment-'+name)).to_have_text('未通过',timeout=15000)
        page.unroute('**/api/health')
        page.unroute('**/api/market/candles?**')
        page.get_by_role('button',name='重新检查连接').click()
        for name in ['api','history']:
            expect(page.get_by_test_id('deployment-'+name)).to_have_text('本次检查通过',timeout=15000)
        assert not errors,errors
        browser.close()
    print('Deployment diagnostics passed: real API/history/fresh socket, retry, acknowledgement-only timeout, stale socket, empty history and API failure')
