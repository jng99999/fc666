"""Real persisted observations, explicit bounded analysis, exact export and failure clearing."""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import ROOT
from core.portfolio.continuity import verify
from tests.browser_terminal import ARGS

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    root='http://127.0.0.1:3000';errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':393,'height':852},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        scenarios=page.request.get(root+'/api/portfolio/scenarios?limit=10').json()['items']
        candidates=[]
        for scene in scenarios:
            snapshots=page.request.get(root+'/api/portfolio/scenarios/'+scene['scenario_id']+'/snapshots?limit=1').json()['items']
            if snapshots:candidates.append((scene,snapshots[0]));break
        assert candidates,'Requires a real saved portfolio scenario'
        scene,summary=candidates[0];id=scene['scenario_id']
        snapshot_url=root+'/api/portfolio/scenarios/'+id+'/snapshots/'+summary['snapshot_id']
        original=page.request.get(snapshot_url).json()
        # Two real captures exercise interval rendering, including same-minute boundaries.
        for _ in range(2):
            response=page.request.post(root+'/api/portfolio/scenarios/'+id+'/snapshots',data={'request_id':str(__import__('uuid').uuid4())})
            assert response.status==200,response.text()
        page.goto(root+'/portfolio/history',wait_until='domcontentloaded')
        page.get_by_role('button',name=scene['definition']['name'],exact=True).click()
        expect(page.get_by_test_id('continuity-result')).to_have_count(0)
        with page.expect_response(lambda response:'/analysis?' in response.url) as response:
            page.get_by_role('button',name='分析已保存历史',exact=True).click()
        assert response.value.status==200,response.value.text()
        report=response.value.json();verify(report)
        result=page.get_by_test_id('continuity-result');expect(result).to_be_visible()
        assert 2<=len(report['points'])<=8 and report['window']['limit']==8
        assert report['edges']
        expect(result).to_contain_text('报价间隔')
        assert all(point['snapshot_id'] in {entry['snapshot_id'] for entry in report['inputs']['snapshots']} for point in report['points'])
        with page.expect_download() as download:page.get_by_role('button',name='下载可比性分析',exact=True).click()
        path=ROOT/'.runtime/browser-portfolio-continuity.json';download.value.save_as(str(path))
        assert json.loads(path.read_text())==report;verify(json.loads(path.read_text()))
        assert page.request.get(snapshot_url).json()==original
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/'.runtime/portfolio-continuity-mobile.png'),full_page=True)
        # Only this browser receives a failed analysis response; persisted sources are unchanged.
        pattern='**/api/portfolio/scenarios/'+id+'/analysis?*'
        page.route(pattern,lambda route:route.fulfill(status=409,json={'detail':'Cannot verify stored history'}))
        page.get_by_role('button',name='分析已保存历史',exact=True).click()
        expect(result).to_have_count(0)
        expect(page.get_by_test_id('portfolio-continuity').get_by_role('alert')).to_contain_text('历史分析不可用 (409)')
        page.unroute(pattern)
        page.reload(wait_until='domcontentloaded')
        page.get_by_role('button',name=scene['definition']['name'],exact=True).click()
        expect(result).to_have_count(0)
        page.get_by_role('button',name='分析已保存历史',exact=True).click()
        expect(result).to_be_visible()
        assert not errors,errors
        print('PASS: actual immutable history; bounded window; no snapshot mutation; exact offline analysis export; failed request clears old metrics; refresh; 393px layout; no JS errors')
        browser.close()
