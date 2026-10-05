"""Real batch admission, cancellation, worker restart, comparison and exports."""
import json
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import stop
from tests.browser_terminal import ARGS
from scripts.reproduce_backtest import reproduce

ROOT=Path(__file__).resolve().parents[1]
def start():subprocess.run([str(ROOT/'.venv/bin/python'),'scripts/dev_services.py','start'],cwd=ROOT,check=True)

if __name__=='__main__':
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip()
    errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000},accept_downloads=True)
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            stop('research')
            page.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
            panel=page.get_by_test_id('batch-research')
            with page.expect_response(lambda r:r.url.endswith('/api/research/batches') and r.request.method=='POST') as response:
                panel.get_by_role('button',name='提交参数批次').click()
            assert response.value.status==202
            batch=response.value.json();assert batch['counts']['QUEUED']==3
            panel.get_by_role('button',name='取消未完成项').click()
            expect(panel.get_by_test_id('batch-comparison')).to_have_attribute('data-status','COMPLETE')
            expect(panel.get_by_role('button',name='下载完整结果')).to_have_count(0)
            assert panel.locator('tbody tr').count()==3
            checks.append('whole queued batch cancellation has no invented successful metrics or downloads')
            with page.expect_response(lambda r:r.url.endswith('/api/research/batches') and r.request.method=='POST') as response:
                panel.get_by_role('button',name='提交参数批次').click()
            accepted=response.value.json();batch_id=accepted['batch_id']
            page.reload(wait_until='domcontentloaded')
            expect(panel.get_by_test_id('batch-comparison')).to_have_attribute('data-status','ACTIVE')
            start()
            expect(panel.get_by_test_id('batch-comparison')).to_have_attribute('data-status','COMPLETE',timeout=30000)
            expect(panel.get_by_role('button',name='下载完整结果')).to_have_count(3)
            results=[]
            for index in range(3):
                with page.expect_download() as download:panel.get_by_role('button',name='下载完整结果').nth(index).click()
                output=ROOT/f'.runtime/browser-batch-{index}.json';download.value.save_as(str(output))
                data=json.loads(output.read_text());assert reproduce(data)==data['run_id'];results.append(data)
            assert len({result['manifest']['data_sha256'] for result in results})==1
            assert len({json.dumps(result['manifest']['instrument'],sort_keys=True) for result in results})==1
            assert len({result['run_id'] for result in results})==3
            assert results[0]['manifest']['parameters']=={'period':10}
            assert results[1]['manifest']['parameters']=={'period':20}
            assert results[2]['manifest']['parameters']=={'fast':10,'slow':20}
            current=page.request.get(f'http://127.0.0.1:3000/api/research/batches/{batch_id}').json()
            assert current['shared_sha256']==accepted['shared_sha256']
            assert {row['request']['as_of'] for row in current['jobs']}=={accepted['jobs'][0]['request']['as_of']}
            for row,result in zip(current['jobs'],results):assert row['metrics']==result['metrics']
            page.reload(wait_until='domcontentloaded')
            expect(panel.get_by_role('button',name='下载完整结果')).to_have_count(3)
            assert page.evaluate("localStorage.getItem('fc666.research.batch.v1')")==batch_id
            checks.append('batch refresh and worker restart restore all members; same data/rules/cutoff; distinct runs; metrics match complete reproducible exports')
            page.screenshot(path=str(ROOT/'.runtime/research-batches.png'),full_page=True)
            page.set_viewport_size({'width':393,'height':852})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'.runtime/research-batches-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            panel.get_by_label('批次参数 JSON',exact=True).fill(json.dumps([{'strategy':'ema_long_flat_v1','parameters':{'period':10}}]*2))
            with page.expect_response(lambda r:r.url.endswith('/api/research/batches') and r.request.method=='POST') as response:
                panel.get_by_role('button',name='提交参数批次').click()
            assert response.value.status==422
            expect(panel.get_by_role('alert')).to_contain_text('批次参数或历史数据不可用')
            expect(panel.get_by_test_id('batch-comparison')).to_have_count(0)
            checks.append('duplicate variants rejected without retaining stale comparison success')
            page.set_viewport_size({'width':393,'height':852})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.request.get('http://127.0.0.1:3000/api/research/batches/not-a-uuid').status==404
            assert page.request.get(f'http://127.0.0.1:3000/api/research/batches/{batch_id}/result').status==404
            assert not errors,errors
        finally:start();browser.close()
    report={'passed':checks,'batch_id':batch_id,'run_ids':[result['run_id'] for result in results],'browser_errors':errors}
    (ROOT/'.runtime/batches-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
