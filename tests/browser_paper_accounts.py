"""Real public history: account discovery, selection, audit and lost acknowledgements."""
import json
import subprocess
from playwright.sync_api import sync_playwright, expect
from tests.browser_terminal import ARGS
from scripts.dev_services import ROOT
from scripts.verify_paper_prefix import verify


if __name__ == '__main__':
    binary = subprocess.check_output(['node','scripts/browser_path.mjs'], cwd=ROOT, text=True).strip()
    errors=[]; checks=[]; realtime_id=None
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(viewport={'width':1440,'height':1000},locale='zh-CN')
        page.on('pageerror',lambda error:errors.append(str(error)))
        root='http://127.0.0.1:3000'
        request={'symbol':'BTCUSDT','timeframe':'1m','limit':10,'strategy':'ema_long_flat_v1','parameters':{'period':2},'config':{'initial_cash':'10000','period':2},'risk':{'max_order_quote':'5000','max_position_quote':'10000','max_drawdown':'1'}}
        try:
            created=[]
            for _ in range(2):
                response=page.request.post(root+'/api/paper/sessions',data=request)
                assert response.status==201,response.text()
                created.append(response.json())
            first,second=created;id=first['session_id'];url=root+'/api/paper/sessions/'+id
            page.goto(root+'/paper',wait_until='domcontentloaded')
            rows=page.get_by_test_id('paper-accounts')
            def row(account_id):return rows.locator(f'tr[data-account-id="{account_id}"]')
            expect(row(id)).to_be_visible()
            row(id).get_by_role('button',name='查看账户',exact=True).click()
            panel=page.get_by_test_id('paper-state');audit=page.get_by_test_id('paper-controls')
            expect(panel).to_have_attribute('data-cursor','0');expect(audit).to_contain_text('暂无控制记录')
            page.get_by_role('button',name='推进模拟一根',exact=True).click()
            expect(panel).to_have_attribute('data-cursor','1');expect(audit).to_contain_text('已执行')
            attempts=[]
            def lose_ack(route):
                attempts.append(route.request.post_data_json)
                response=route.fetch();assert response.status==200;route.abort('failed')
            page.route('**/api/paper/sessions/*/command',lose_ack)
            page.get_by_role('button',name='推进模拟一根',exact=True).click()
            expect(panel).to_have_attribute('data-cursor','2')
            expect(page.locator('.paper-page > [role=alert]')).to_contain_text('操作未确认')
            assert len(attempts)==1
            page.unroute('**/api/paper/sessions/*/command',lose_ack)
            response=page.request.post(url+'/command',data={'expected_revision':2,'action':'step'})
            assert response.status==200
            page.get_by_role('button',name='推进模拟一根',exact=True).click()
            expect(panel).to_have_attribute('data-cursor','3');expect(audit).to_contain_text('版本冲突')
            events=page.request.get(url+'/controls').json()['items']
            assert len(events)==4 and [event['outcome'] for event in events].count('APPLIED')==3
            assert events[0]['outcome']=='CONFLICT'
            state=page.request.get(url).json();verify(state)
            checks.append('account selected from database list; lost acknowledgement commits once; conflict is audited and cannot duplicate execution')
            row(second['session_id']).get_by_role('button',name='查看账户',exact=True).click()
            expect(panel).to_have_attribute('data-cursor','0');expect(audit).to_contain_text('暂无控制记录')
            page.reload(wait_until='domcontentloaded')
            expect(row(second['session_id']).get_by_role('button',name='当前账户')).to_be_visible()
            assert page.evaluate("localStorage.getItem('fc666.paper.session.v1')")==second['session_id']
            response=page.request.post(url+'/command',data={'expected_revision':3,'action':'step','count':10})
            assert response.status==200 and response.json()['status']=='ENDED'
            page.get_by_label('账户状态筛选',exact=True).select_option('ENDED')
            expect(row(id)).to_be_visible();expect(row(second['session_id'])).to_have_count(0)
            row(id).get_by_role('button',name='查看账户',exact=True).click()
            expect(panel).to_have_attribute('data-cursor','10')
            expect(page.get_by_role('button',name='推进模拟一根',exact=True)).to_be_disabled()
            page.set_viewport_size({'width':393,'height':852})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'.runtime/paper-accounts-mobile.png'),full_page=True)
            checks.append('switching accounts restores fixed state; refresh keeps selected account; ended filter retains completed records; mobile layout fits')
            # Create then stop only this test stream; existing accounts stay untouched.
            response=page.request.post(root+'/api/paper/streams',data={**request,'limit':8})
            assert response.status==201;stream=response.json();realtime_id=stream['session_id']
            stream_url=root+'/api/paper/streams/'+realtime_id
            for _ in range(3):
                stream=page.request.get(stream_url).json()
                response=page.request.post(stream_url+'/command',data={'expected_revision':stream['revision'],'action':'stop'})
                if response.status==200:break
                assert response.status==409
            assert response.status==200
            page.goto(root+'/paper/realtime',wait_until='domcontentloaded')
            page.get_by_label('账户状态筛选',exact=True).select_option('STOPPED')
            rows=page.get_by_test_id('paper-accounts')
            row(realtime_id).get_by_role('button',name='查看账户',exact=True).click()
            expect(page.get_by_test_id('paper-stream-state')).to_have_attribute('data-status','STOPPED')
            expect(page.get_by_test_id('paper-controls')).to_contain_text('停止账户')
            expect(page.get_by_test_id('paper-controls')).to_contain_text('已执行')
            page.reload(wait_until='domcontentloaded')
            expect(page.get_by_test_id('paper-stream-state')).to_have_attribute('data-status','STOPPED')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            checks.append('stopped realtime account remains discoverable with its immutable controls and survives refresh')
        finally:
            if realtime_id:
                for _ in range(3):
                    stream=page.request.get(root+'/api/paper/streams/'+realtime_id).json()
                    if stream.get('status')=='STOPPED':break
                    response=page.request.post(root+'/api/paper/streams/'+realtime_id+'/command',data={'expected_revision':stream['revision'],'action':'stop'})
                    if response.status==200:break
                    assert response.status==409
            browser.close()
    report={'passed':checks,'browser_errors':errors,'historical_ids':[item['session_id'] for item in created],'realtime_id':realtime_id}
    (ROOT/'.runtime/paper-accounts-browser-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
