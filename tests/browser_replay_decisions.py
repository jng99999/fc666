"""Real replay target events, causal export, reset identity and frozen parameters."""
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright,expect
from tests.browser_terminal import ARGS
from scripts.verify_replay_prefix import verify
ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:3000/replay',wait_until='domcontentloaded')
        page.get_by_label('回放周期',exact=True).select_option('1m')
        page.get_by_label('回放柱数',exact=True).fill('8')
        page.get_by_label('回放均线周期',exact=True).fill('2')
        page.get_by_label('回放策略',exact=True).select_option('sma_long_flat_v1')
        page.get_by_label('回放 SMA 快周期',exact=True).fill('2')
        page.get_by_label('回放 SMA 慢周期',exact=True).fill('3')
        with page.expect_response(lambda response:response.url.endswith('/api/replay/sessions') and response.request.method=='POST') as response:page.get_by_role('button',name='创建固定回放').click()
        created=response.value.json();assert response.value.status==201
        assert created['manifest']['version']=='closed-bar-replay-v2'
        assert created['manifest']['parameters']=={'fast':2,'slow':3}
        assert created['decisions']==[]
        panel=page.get_by_test_id('replay-decisions')
        for index in range(1,5):
            with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进一根',exact=True).click()
            observed=response.value.json()
            expect(panel).to_have_attribute('data-count',str(max(0,index-2)))
            for offset,event in enumerate(observed['decisions'],start=2):
                values=[Fraction(bar['close']) for bar in observed['candles']]
                assert event['target']==('LONG' if sum(values[offset-1:offset+1])/2>sum(values[offset-2:offset+1])/3 else 'FLAT')
                assert datetime.fromisoformat(event['available_at'])<=datetime.fromisoformat(observed['clock'])
                assert event['execution']=='NOT_IMPLEMENTED'
        with page.expect_download() as download:page.get_by_role('button',name='下载已确认前缀',exact=True).click()
        output=ROOT/'.runtime/browser-replay-decision-prefix.json';download.value.save_as(str(output))
        exported=json.loads(output.read_text());assert exported==observed
        assert verify(exported)=={'bars':4,'decisions':2}
        assert 'dataset' not in exported and 'snapshot' not in exported
        checks.append('SMA warmup and reached-close events match independent rational reference; downloaded raw reached prefix matches response and verifies offline')
        ids=[event['event_id'] for event in exported['decisions']]
        page.get_by_role('button',name='从头重置',exact=True).click()
        expect(panel).to_have_attribute('data-count','0')
        for _ in range(4):
            with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进一根',exact=True).click()
            repeated=response.value.json()
        assert [event['event_id'] for event in repeated['decisions']]==ids
        page.reload(wait_until='domcontentloaded')
        expect(panel).to_have_attribute('data-count','2')
        expect(page.get_by_label('回放策略',exact=True)).to_have_value('sma_long_flat_v1')
        expect(page.get_by_label('回放 SMA 慢周期',exact=True)).to_have_value('3')
        # Editing the next-session form cannot change the current frozen strategy.
        page.get_by_label('回放 SMA 慢周期',exact=True).fill('7')
        with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进一根',exact=True).click()
        assert response.value.json()['manifest']['parameters']=={'fast':2,'slow':3}
        assert response.value.json()['decisions'][:2]==repeated['decisions']
        checks.append('reset reproduces identical event IDs; refresh restores fixed strategy; next-session form changes cannot alter current decisions')
        page.screenshot(path=str(ROOT/'.runtime/replay-decisions.png'),full_page=True)
        page.set_viewport_size({'width':393,'height':852})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/replay-decisions-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_label('回放策略',exact=True).select_option('ema_long_flat_v1')
        with page.expect_response(lambda response:response.url.endswith('/api/replay/sessions') and response.request.method=='POST') as response:page.get_by_role('button',name='创建固定回放').click()
        assert response.value.status==201 and response.value.json()['manifest']['parameters']=={'period':2}
        for index in range(1,4):
            with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进一根',exact=True).click()
        assert verify(response.value.json())=={'bars':3,'decisions':2}
        expect(panel).to_contain_text('ema_long_flat_v1')
        checks.append('EMA selection binds shared indicator period; reached prefix and events reproduce offline; populated mobile layout has no page overflow')
        assert not errors,errors
        browser.close()
    report={'passed':checks,'session_id':created['session_id'],'event_ids':ids,'browser_errors':errors}
    (ROOT/'.runtime/replay-decisions-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
