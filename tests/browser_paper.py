"""Real historical paper UI, CAS/lost-ack, halt, export and refresh verification."""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from tests.browser_terminal import ARGS
from scripts.verify_paper_prefix import verify
ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    errors=[];checks=[];requests=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:requests.append(request.url))
        page.goto('http://127.0.0.1:3000/paper',wait_until='domcontentloaded')
        page.get_by_label('模拟周期',exact=True).select_option('1m')
        page.get_by_label('模拟柱数',exact=True).fill('40')
        page.get_by_label('模拟均线周期',exact=True).fill('2')
        page.get_by_label('单笔买入上限 USDT',exact=True).fill('1000000000')
        page.get_by_label('买入后持仓上限 USDT',exact=True).fill('1000000000')
        page.get_by_label('回撤停止阈值（0..1）',exact=True).fill('1')
        page.get_by_label('前柱成交量参与率（0..0.1）',exact=True).fill('0.1')
        with page.expect_response(lambda response:response.url.endswith('/api/paper/sessions') and response.request.method=='POST') as response:page.get_by_role('button',name='创建独立模拟账户',exact=True).click()
        initial=response.value.json();assert response.value.status==201
        session_id=initial['session_id'];url=f'http://127.0.0.1:3000/api/paper/sessions/{session_id}'
        panel=page.get_by_test_id('paper-state');expect(panel).to_have_attribute('data-cursor','0')
        assert verify(initial)=={'bars':0,'orders':0,'fills':0}
        for index in range(1,4):
            with page.expect_response(lambda response:response.url.endswith('/command')) as response:page.get_by_role('button',name='推进模拟一根',exact=True).click()
            state=response.value.json();verify(state)
            expect(panel).to_have_attribute('data-cursor',str(index))
        # Lost acknowledgement after commit: one POST only, then GET recovery.
        attempts=[]
        def lose_ack(route):
            attempts.append(route.request.post_data_json)
            committed=route.fetch();assert committed.status==200
            route.abort('failed')
        page.route('**/api/paper/sessions/*/command',lose_ack)
        page.get_by_role('button',name='推进模拟一根',exact=True).click()
        expect(panel).to_have_attribute('data-cursor','4')
        expect(page.locator('.paper-page [role=alert]')).to_contain_text('操作未确认')
        assert len(attempts)==1
        page.unroute('**/api/paper/sessions/*/command',lose_ack)
        # A second tab/client advances; stale revision is rejected and synchronized.
        response=page.request.post(url+'/command',data={'expected_revision':4,'action':'step'})
        assert response.status==200
        page.get_by_role('button',name='推进模拟一根',exact=True).click()
        expect(panel).to_have_attribute('data-cursor','5')
        expect(page.locator('.paper-page [role=alert]')).to_contain_text('版本冲突')
        state=page.request.get(url).json();assert state['revision']==5;verify(state)
        checks.append('zero-bar account and causal step ledger verify offline; lost acknowledgement commits once and GET recovers; stale revision cannot duplicate fills')
        # Finish in bounded server commands, then refresh the browser from persisted state.
        while state['status']!='ENDED':
            response=page.request.post(url+'/command',data={'expected_revision':state['revision'],'action':'step','count':10})
            assert response.status==200;state=response.json();verify(state)
        assert len(state['fills'])>0 and any(fill['side']=='BUY' for fill in state['fills'])
        page.reload(wait_until='domcontentloaded');expect(panel).to_have_attribute('data-cursor','40')
        expect(panel).to_have_attribute('data-fills',str(len(state['fills'])))
        expect(page.get_by_role('button',name='推进模拟一根',exact=True)).to_be_disabled()
        with page.expect_download() as download:page.get_by_role('button',name='下载模拟账本前缀',exact=True).click()
        output=ROOT/'.runtime/browser-paper-prefix.json';download.value.save_as(str(output))
        exported=json.loads(output.read_text());assert exported==state;verify(exported)
        assert 'snapshot' not in exported and 'dataset' not in exported
        fills=state['fills']
        checks.append('real historical fills persist on reload; ended account cannot advance; raw ledger download reproduces account, risk, orders and fills offline')
        # Reset explicitly clears balances, then halt before any bar prevents all new entries.
        page.get_by_role('button',name='重置资金并从头模拟',exact=True).click()
        expect(panel).to_have_attribute('data-cursor','0');expect(panel).to_have_attribute('data-fills','0')
        page.get_by_role('button',name='停止新买入',exact=True).click()
        expect(panel).to_have_attribute('data-halted','true')
        page.reload(wait_until='domcontentloaded');expect(panel).to_have_attribute('data-halted','true')
        state=page.request.get(url).json()
        while state['status']!='ENDED':
            response=page.request.post(url+'/command',data={'expected_revision':state['revision'],'action':'step','count':10})
            assert response.status==200;state=response.json();verify(state)
        assert state['fills']==[] and all(order['reason']=='MANUAL_HALT' for order in state['orders']) and state['account']['cash']=='10000'
        page.reload(wait_until='domcontentloaded');expect(panel).to_have_attribute('data-cursor','40')
        expect(panel).to_contain_text('手动停止买入')
        checks.append('reset clears inventory and fills; manual halt survives refresh and blocks all historical buy attempts without spending cash')
        page.screenshot(path=str(ROOT/'.runtime/paper-desktop.png'),full_page=True)
        page.set_viewport_size({'width':393,'height':852})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/paper-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        # New-account risk rejection, with current account controls frozen.
        page.get_by_label('单笔买入上限 USDT',exact=True).fill('1')
        with page.expect_response(lambda response:response.url.endswith('/api/paper/sessions') and response.request.method=='POST') as response:page.get_by_role('button',name='创建独立模拟账户',exact=True).click()
        assert response.value.status==201;state=response.value.json()
        risk_id=state['session_id'];risk_url=f'http://127.0.0.1:3000/api/paper/sessions/{risk_id}'
        while state['status']!='ENDED':
            response=page.request.post(risk_url+'/command',data={'expected_revision':state['revision'],'action':'step','count':10})
            assert response.status==200;state=response.json();verify(state)
        assert state['fills']==[] and any(order['reason']=='MAX_ORDER_QUOTE' for order in state['orders'])
        page.reload(wait_until='domcontentloaded');expect(panel).to_contain_text('超出单笔买入金额')
        assert not any('/market/' in url or '/stream/' in url for url in requests)
        assert not errors,errors
        checks.append('strict frozen order limit rejects real historical entries; populated mobile page fits; no realtime/full market requests or JavaScript errors')
        browser.close()
    report={'passed':checks,'session_id':session_id,'risk_session_id':risk_id,'real_fill_count':len(fills),'browser_errors':errors}
    (ROOT/'.runtime/paper-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
