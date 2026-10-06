"""Real public minute bars, durable autonomous paper, worker outage and UI controls."""
import json
import subprocess
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.browser_terminal import ARGS
from scripts.dev_services import stop,ROOT
from scripts.verify_paper_stream import verify

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    errors=[];checks=[];worker_stopped=False;session_id=None
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.goto('http://127.0.0.1:3000/paper/realtime',wait_until='domcontentloaded')
            expect(page.get_by_test_id('paper-worker')).to_have_attribute('data-healthy','true')
            page.get_by_label('实时模拟交易对',exact=True).select_option('ETHUSDT')
            page.get_by_label('预热柱数',exact=True).fill('8')
            page.get_by_label('实时均线周期',exact=True).fill('2')
            page.get_by_label('实时前柱量参与率（0..0.1）',exact=True).fill('0.1')
            page.get_by_label('实时单笔买入上限 USDT',exact=True).fill('1000000000')
            page.get_by_label('实时持仓买入上限 USDT',exact=True).fill('1000000000')
            page.get_by_label('实时回撤停止阈值（0..1）',exact=True).fill('1')
            with page.expect_response(lambda response:response.url.endswith('/api/paper/streams') and response.request.method=='POST') as response:page.get_by_role('button',name='创建实时模拟账户',exact=True).click()
            assert response.value.status==201;state=response.value.json();verify(state)
            session_id=state['session_id'];url=f'http://127.0.0.1:3000/api/paper/streams/{session_id}'
            assert state['fills']==[] and state['accepted_bars']==0 and state['account']['cash']=='10000'
            panel=page.get_by_test_id('paper-stream-state');expect(panel).to_have_attribute('data-status','RUNNING')
            # Wait for an actual new exchange minute close, never inject product candles.
            deadline=time.monotonic()+100
            while state['accepted_bars']<1:
                assert state['status']=='RUNNING',state['feed']
                if time.monotonic()>deadline:raise AssertionError('No real finalized public minute bar accepted in 100s')
                page.wait_for_timeout(1000);state=page.request.get(url).json()
            verify(state)
            assert state['observations'][0]['gate']=='ELIGIBLE'
            expect(panel).to_have_attribute('data-accepted',str(state['accepted_bars']))
            for fill in state['fills']:
                assert fill['decision_at']<fill['execution_at']<=fill['observed_at']
            live_fills=len(state['fills'])
            checks.append('warmup makes no retrospective fills; worker automatically accepts a real new public minute close with eligible timing; acknowledged ledger reproduces offline')
            # Stop only the owned paper process, leaving market ingestion running.
            stop('paper');worker_stopped=True
            expect(page.get_by_test_id('paper-worker')).to_have_attribute('data-healthy','false',timeout=15000)
            before=page.request.get(url).json();last_close=datetime.fromisoformat(before['clock'])
            page.reload(wait_until='domcontentloaded');expect(panel).to_have_attribute('data-accepted',str(before['accepted_bars']))
            target=last_close+timedelta(seconds=60+25)
            deadline=time.monotonic()+110
            while datetime.now(timezone.utc)<target:
                if time.monotonic()>deadline:raise AssertionError('Minute boundary timeout')
                page.wait_for_timeout(1000)
            during=page.request.get(url).json()
            assert during['observations']==before['observations'] and during['fills']==before['fills'] and during['account']==before['account']
            subprocess.run(['bash','scripts/start.sh'],cwd=ROOT,check=True);worker_stopped=False
            deadline=time.monotonic()+15;state=during
            while state['accepted_bars']==before['accepted_bars']:
                if time.monotonic()>deadline:raise AssertionError('Restarted worker did not recover finalized backlog')
                page.wait_for_timeout(500);state=page.request.get(url).json()
            recovered=state['observations'][before['accepted_bars']:]
            assert recovered and all(row['gate']=='STALE_BAR' for row in recovered)
            assert state['fills']==before['fills'] and state['account']['cash']==before['account']['cash'] and state['account']['quantity']==before['account']['quantity']
            verify(state);expect(panel).to_have_attribute('data-accepted',str(state['accepted_bars']))
            checks.append('owned worker outage freezes cash/inventory/fills while public market continues; page restores persisted account; restart accepts real backlog as stale and never fabricates outage fills')
            # Lost acknowledgement commits a pause once and syncs without POST retry.
            attempts=[]
            def lose_ack(route):
                attempts.append(route.request.post_data_json);committed=route.fetch();assert committed.status==200;route.abort('failed')
            page.route('**/api/paper/streams/*/command',lose_ack)
            page.get_by_role('button',name='暂停实时成交',exact=True).click()
            expect(panel).to_have_attribute('data-status','PAUSED')
            expect(page.locator('.paper-page [role=alert]')).to_contain_text('操作未确认')
            assert len(attempts)==1;page.unroute('**/api/paper/streams/*/command',lose_ack)
            page.get_by_role('button',name='恢复实时模拟',exact=True).click();expect(panel).to_have_attribute('data-status','RUNNING')
            page.get_by_role('button',name='停止实时新买入',exact=True).click();expect(panel).to_have_attribute('data-halted','true')
            page.reload(wait_until='domcontentloaded');expect(panel).to_have_attribute('data-halted','true')
            expect(page.get_by_label('实时均线周期',exact=True)).to_have_value('2')
            expect(page.get_by_label('实时模拟交易对',exact=True)).to_have_value('ETHUSDT')
            with page.expect_download() as download:page.get_by_role('button',name='下载实时模拟账本',exact=True).click()
            output=ROOT/'.runtime/browser-paper-stream.json';download.value.save_as(str(output))
            exported=json.loads(output.read_text());verify(exported)
            assert exported['session_id']==session_id and exported['risk']['entry_halted']
            page.screenshot(path=str(ROOT/'.runtime/paper-stream-desktop.png'),full_page=True)
            page.set_viewport_size({'width':393,'height':852});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'.runtime/paper-stream-mobile.png'),full_page=True)
            page.get_by_role('button',name='停止此实时账户',exact=True).click();expect(panel).to_have_attribute('data-status','STOPPED')
            expect(page.get_by_role('button',name='暂停实时成交',exact=True)).to_be_disabled()
            assert not errors,errors
            checks.append('lost pause acknowledgement commits once; resume and halt controls persist on refresh; raw stream verifies; populated mobile layout fits; stop is terminal')
        finally:
            if worker_stopped:subprocess.run(['bash','scripts/start.sh'],cwd=ROOT,check=True)
            if session_id:
                # Own only this test account; known revision conflicts may be resynchronized.
                for _ in range(3):
                    state=page.request.get(f'http://127.0.0.1:3000/api/paper/streams/{session_id}').json()
                    if state.get('status')=='STOPPED':break
                    response=page.request.post(f'http://127.0.0.1:3000/api/paper/streams/{session_id}/command',data={'expected_revision':state['revision'],'action':'stop'})
                    if response.status==200:break
                    assert response.status==409
            browser.close()
    report={'passed':checks,'session_id':session_id,'accepted_bars':state['accepted_bars'],'initial_realtime_fill_count':live_fills,'browser_errors':errors}
    (ROOT/'.runtime/paper-stream-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
