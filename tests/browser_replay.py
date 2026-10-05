"""Real prefix-only replay, pause/resume, CAS conflicts and lost acknowledgements."""
import json
from datetime import datetime
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright,expect

from tests.browser_terminal import ARGS
ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    checks=[];errors=[];sockets=[];market_requests=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('websocket',lambda socket:sockets.append(socket.url))
        page.on('request',lambda request:market_requests.append(request.url) if '/api/market/' in request.url else None)
        page.goto('http://127.0.0.1:3000/replay',wait_until='domcontentloaded')
        page.get_by_label('回放周期',exact=True).select_option('1m')
        page.get_by_label('回放柱数',exact=True).fill('8')
        page.get_by_label('回放均线周期',exact=True).fill('2')
        with page.expect_response(lambda response:response.url.endswith('/api/replay/sessions') and response.request.method=='POST') as response:page.get_by_role('button',name='创建固定回放').click()
        assert response.value.status==201
        initial=response.value.json();session_id=initial['session_id'];prefix=f'http://127.0.0.1:3000/api/replay/sessions/{session_id}'
        assert initial['cursor']==0 and initial['candles']==initial['indicators']==[]
        assert 'dataset' not in initial and 'snapshot' not in initial
        state=page.get_by_test_id('replay-state')
        expect(state).to_have_attribute('data-cursor','0')
        expect(page.get_by_test_id('replay-close')).to_have_text('尚未推进')
        for index in range(1,3):
            with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进一根',exact=True).click()
            observed=response.value.json()
            assert observed['cursor']==len(observed['candles'])==len(observed['indicators'])==index
            assert observed['manifest']['snapshot_sha256']==initial['manifest']['snapshot_sha256']
            clock=datetime.fromisoformat(observed['clock'])
            assert all(datetime.fromisoformat(bar['close_time'])<=clock for bar in observed['candles'])
            assert all(datetime.fromisoformat(row['available_at'])<=clock for row in observed['indicators'])
            expect(page.get_by_test_id('replay-close')).to_have_text(observed['candles'][-1]['close'])
        canvas_count=page.get_by_test_id('replay-chart').locator('canvas').count()
        page.get_by_role('button',name='回放指标',exact=True).click()
        page.wait_for_function(f"document.querySelectorAll('[data-testid=replay-chart] canvas').length>{canvas_count}")
        checks.append('zero-bar initial view; every step returns only reached candles and causal indicators; chart panes use reached prefix; no live market requests')
        page.get_by_label('回放速度',exact=True).select_option('4')
        page.get_by_role('button',name='播放回放',exact=True).click()
        page.wait_for_function("Number(document.querySelector('[data-testid=replay-state]').dataset.cursor)>=3")
        page.get_by_role('button',name='暂停回放',exact=True).click()
        expect(state).to_have_attribute('data-playing','false')
        expect(page.get_by_role('button',name='推进一根',exact=True)).to_be_enabled()
        paused=page.request.get(prefix).json();page.wait_for_timeout(700)
        assert page.request.get(prefix).json()==paused
        page.reload(wait_until='domcontentloaded')
        expect(state).to_have_attribute('data-cursor',str(paused['cursor']))
        expect(state).to_have_attribute('data-playing','false')
        expect(page.get_by_label('回放速度',exact=True)).to_have_value('4')
        checks.append('target speed playback; pause stops subsequent advancement; refresh restores acknowledged cursor and defaults paused')
        # Another client advances while this page holds a stale revision.
        remote=page.request.post(prefix+'/command',data={'expected_revision':paused['revision'],'action':'step'}).json()
        with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进一根',exact=True).click()
        assert response.value.status==409
        expect(page.locator('.replay-page p[role=alert]')).to_contain_text('版本冲突')
        expect(state).to_have_attribute('data-cursor',str(remote['cursor']))
        assert page.request.get(prefix).json()['cursor']==remote['cursor']
        checks.append('stale-tab revision conflict pauses and synchronizes without duplicate advancement')
        # Apply exactly one server step, then lose its response; never auto-repeat POST.
        intercepted=[]
        def lose_ack(route):
            acknowledgement=route.fetch();intercepted.append(acknowledgement.json());route.abort('failed')
        page.route('**/api/replay/sessions/*/command',lose_ack)
        page.get_by_role('button',name='推进一根',exact=True).click()
        page.wait_for_function(f"Number(document.querySelector('[data-testid=replay-state]').dataset.cursor)=={remote['cursor']+1}")
        expect(state).to_have_attribute('data-playing','false')
        page.wait_for_timeout(600)
        assert len(intercepted)==1
        assert page.request.get(prefix).json()['cursor']==remote['cursor']+1
        page.unroute('**/api/replay/sessions/*/command',lose_ack)
        checks.append('lost acknowledgement recovers committed server state with no automatic mutation retry')
        page.get_by_role('button',name='播放回放',exact=True).click()
        expect(state).to_have_attribute('data-status','ENDED',timeout=10000)
        expect(state).to_have_attribute('data-cursor','8')
        expect(state).to_have_attribute('data-playing','false')
        expect(page.get_by_role('button',name='推进一根',exact=True)).to_be_disabled()
        final=page.request.get(prefix).json()
        page.screenshot(path=str(ROOT/'.runtime/replay-desktop.png'),full_page=True)
        page.set_viewport_size({'width':393,'height':852})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/replay-mobile.png'),full_page=True)
        page.get_by_role('button',name='从头重置',exact=True).click()
        expect(state).to_have_attribute('data-cursor','0')
        reset=page.request.get(prefix).json()
        assert reset['candles']==reset['indicators']==[]
        assert reset['manifest']['snapshot_sha256']==initial['manifest']['snapshot_sha256']
        expect(page.get_by_test_id('replay-close')).to_have_text('尚未推进')
        assert page.request.get('http://127.0.0.1:3000/api/replay/sessions/not-uuid').status==404
        assert page.request.get(prefix+'/snapshot').status==404
        assert not sockets and not market_requests,(sockets,market_requests)
        assert not errors,errors
        checks.append('ended playback stops; populated mobile layout; reset clears future chart inputs; arbitrary/full snapshot proxy paths rejected')
        browser.close()
    report={'passed':checks,'session_id':session_id,'snapshot_sha256':initial['manifest']['snapshot_sha256'],'browser_errors':errors}
    (ROOT/'.runtime/replay-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
