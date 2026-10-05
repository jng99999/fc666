"""Real database/browser queued cancellation and worker restart survival."""
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
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],cwd=ROOT,text=True).strip();errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS);page=browser.new_page(locale='zh-CN',viewport={'width':1440,'height':1000},accept_downloads=True);page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            stop('research')
            page.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
            with page.expect_response(lambda r:r.url.endswith('/api/research/jobs') and r.request.method=='POST') as response:page.get_by_role('button',name='运行历史回测').click()
            task=response.value.json();assert response.value.status==202 and task['status']=='QUEUED'
            expect(page.get_by_test_id('research-task')).to_have_attribute('data-status','QUEUED')
            page.get_by_role('button',name='取消任务',exact=True).click()
            expect(page.get_by_test_id('research-task')).to_have_attribute('data-status','CANCELLED',timeout=10000)
            expect(page.get_by_test_id('backtest-result')).to_have_count(0)
            checks.append('queued cancellation while worker stopped has no successful result')
            with page.expect_response(lambda r:r.url.endswith('/api/research/jobs') and r.request.method=='POST') as response:page.get_by_role('button',name='运行历史回测').click()
            task=response.value.json();job_id=task['job_id']
            page.reload(wait_until='domcontentloaded');expect(page.get_by_test_id('research-task')).to_have_attribute('data-status','QUEUED')
            start()
            expect(page.get_by_test_id('research-task')).to_have_attribute('data-status','SUCCEEDED',timeout=30000)
            expect(page.get_by_test_id('backtest-result')).to_contain_text('已完成')
            with page.expect_download() as download:page.get_by_role('button',name='下载结果与清单').click()
            output=ROOT/'.runtime/browser-job-export.json';download.value.save_as(str(output));run_id=reproduce(json.loads(output.read_text()))
            page.reload(wait_until='domcontentloaded');expect(page.get_by_test_id('backtest-result')).to_contain_text('已完成')
            assert page.evaluate("localStorage.getItem('fc666.research.activeJob.v1')")==job_id
            checks.append('queued job survives page reload and worker restart; persisted result restores and reproduces')
            page.screenshot(path=str(ROOT/'.runtime/research-jobs.png'),full_page=True)
            assert not errors,errors
        finally:start();browser.close()
    report={'passed':checks,'job_id':job_id,'run_id':run_id,'browser_errors':errors};(ROOT/'.runtime/jobs-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
