"""Real fixed-parameter chronological comparison and immutable composite export."""
import json
from datetime import datetime
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
            panel=page.get_by_test_id('holdout-research')
            with page.expect_response(lambda response:response.url.endswith('/api/research/holdouts') and response.request.method=='POST') as response:panel.get_by_role('button',name='运行分段研究').click()
            assert response.value.status==202
            expect(panel.get_by_test_id('holdout-task')).to_have_attribute('data-status','QUEUED')
            panel.get_by_role('button',name='取消分段任务').click()
            expect(panel.get_by_test_id('holdout-task')).to_have_attribute('data-status','CANCELLED')
            expect(panel.get_by_test_id('holdout-comparison')).to_have_count(0)
            checks.append('queued holdout cancellation publishes neither segment')
            with page.expect_response(lambda response:response.url.endswith('/api/research/holdouts') and response.request.method=='POST') as response:panel.get_by_role('button',name='运行分段研究').click()
            task=response.value.json();job_id=task['job_id']
            page.reload(wait_until='domcontentloaded')
            expect(panel.get_by_test_id('holdout-task')).to_have_attribute('data-status','QUEUED')
            start()
            expect(panel.get_by_test_id('holdout-comparison')).to_be_visible(timeout=30000)
            expect(panel.get_by_test_id('holdout-comparison').locator('tbody tr')).to_have_count(2)
            with page.expect_download() as download:panel.get_by_role('button',name='下载完整分段结果').click()
            export=ROOT/'.runtime/browser-holdout-export.json';download.value.save_as(str(export))
            result=json.loads(export.read_text());run_id=reproduce(result)
            assert result['manifest']['train_bars']==60 and result['manifest']['test_bars']==60
            assert result['train']['manifest']['parameters']==result['test']['manifest']['parameters']=={'period':20}
            assert result['train']['manifest']['end']==result['manifest']['split_at']==result['test']['manifest']['start']
            for segment in [result['train'],result['test']]:
                assert segment['equity'][0]['cash']==result['manifest']['config']['initial_cash']
                assert segment['equity'][0]['quantity']=='0'
                assert datetime.fromisoformat(segment['signals'][0]['available_at'])==datetime.fromisoformat(segment['dataset'][19]['close_time'])
                assert all(fill['execution_at']>=segment['manifest']['start'] for fill in segment['fills'])
                assert reproduce(segment)==segment['run_id']
            persisted=page.request.get(f'http://127.0.0.1:3000/api/research/jobs/{job_id}/result').json()
            assert result==persisted
            page.reload(wait_until='domcontentloaded')
            expect(panel.get_by_test_id('holdout-comparison')).to_be_visible()
            assert page.evaluate("localStorage.getItem('fc666.research.holdout.v1')")==job_id
            checks.append('worker/page restart restores two independent cold-start segments; exact composite and child export reproduction')
            # Same dataset cutoff and fixed parameters must reproduce the same composite run.
            page.get_by_label('截止时间（含时区，可空）',exact=True).fill(result['test']['manifest']['end'])
            with page.expect_response(lambda response:response.url.endswith('/result') and response.request.method=='GET') as response:panel.get_by_role('button',name='运行分段研究').click()
            assert response.value.json()['run_id']==run_id
            page.screenshot(path=str(ROOT/'.runtime/research-holdout.png'),full_page=True)
            page.set_viewport_size({'width':393,'height':852})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ROOT/'.runtime/research-holdout-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            panel.get_by_label('样本内柱数',exact=True).fill('2')
            with page.expect_response(lambda response:response.url.endswith('/api/research/holdouts') and response.request.method=='POST') as response:panel.get_by_role('button',name='运行分段研究').click()
            assert response.value.status==409
            expect(panel.get_by_role('alert')).to_contain_text('lookback plus one')
            expect(panel.get_by_test_id('holdout-comparison')).to_have_count(0)
            assert page.request.get('http://127.0.0.1:3000/api/research/holdouts/unknown').status==404
            checks.append('fixed-cutoff determinism, populated mobile layout, insufficient warmup rejection clears prior success')
            assert not errors,errors
        finally:start();browser.close()
    report={'passed':checks,'job_id':job_id,'run_id':run_id,'browser_errors':errors}
    (ROOT/'.runtime/holdout-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
