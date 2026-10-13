"""Read-only batch replay controls using retained actual research jobs."""
import json,subprocess,urllib.request
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.storage.models import ResearchJobRecord as Job
from playwright.sync_api import sync_playwright,expect
from tests.browser_terminal import ARGS

if __name__=='__main__':
    engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
    try:
        with Session(engine) as session:
            job=session.scalar(select(Job).where(Job.status=='SUCCEEDED',Job.request['batch_id'].as_string().is_not(None)).order_by(Job.created_at.desc()).limit(1))
            if job is None:raise RuntimeError('An existing successful batch member is required; no fixture is written to the main database')
            batch_id=job.request['batch_id']
    finally:engine.dispose()
    with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/research/batches/'+batch_id) as response:batch=json.load(response)
    successful=[row for row in batch['jobs'] if row['status']=='SUCCEEDED' and row['run_id']]
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],text=True).strip();errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN',accept_downloads=True)
        page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.add_init_script('localStorage.clear();localStorage.setItem("fc666.research.batch.v1",'+json.dumps(batch_id)+');')
            page.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
            panel=page.get_by_test_id('batch-comparison');expect(panel).to_be_visible(timeout=30000)
            controls=panel.get_by_test_id('research-verification');expect(controls).to_have_count(len(successful))
            for index,row in enumerate(successful):
                control=controls.nth(index)
                with page.expect_response(lambda r:r.url.endswith('/'+row['job_id']+'/verify')) as response:
                    control.get_by_role('button',name='复验冻结结果',exact=True).click()
                assert response.value.status==200,response.value.text()
                expect(control).to_contain_text('复验通过',timeout=15000)
            # A failed member recheck cannot clear or grant success to another row.
            page.route('**/api/research/jobs/'+successful[0]['job_id']+'/verify',lambda route:route.fulfill(status=409,json={'detail':'Verification failed'}))
            controls.nth(0).get_by_role('button',name='复验冻结结果',exact=True).click()
            expect(controls.nth(0).get_by_role('alert')).to_contain_text('复验未通过')
            if len(successful)>1:expect(controls.nth(1)).to_contain_text('复验通过')
            # A mismatched downloaded result must not be presented as this row's report.
            row_element=panel.locator('tbody tr').nth(next(i for i,row in enumerate(batch['jobs']) if row['job_id']==successful[0]['job_id']))
            page.route('**/api/research/jobs/'+successful[0]['job_id']+'/result',lambda route:route.fulfill(status=200,json={'run_id':'different','trading_enabled':False}))
            row_element.get_by_role('button',name='下载完整结果').click()
            expect(page.get_by_test_id('batch-research').get_by_role('alert').first).to_contain_text('结果格式不可用')
            assert not errors,errors
            print(f'Batch browser acceptance passed: {len(successful)} actual member replays, isolated failure state and mismatched download rejection; no data writes')
        finally:browser.close()
