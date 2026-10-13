"""Read-only browser acceptance against an existing retained successful job."""
import json,subprocess,urllib.request
from playwright.sync_api import sync_playwright,expect
from tests.browser_terminal import ARGS

if __name__=='__main__':
    with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/research/jobs') as response:jobs=json.load(response)
    task=next(job for job in jobs if job['status']=='SUCCEEDED' and job['request'].get('research_type') in [None,'walk_forward','holdout'])
    kind=task['request'].get('research_type')
    key={None:'fc666.research.activeJob.v1','walk_forward':'fc666.research.walk-forward.v1','holdout':'fc666.research.holdout.v1'}[kind]
    panel={None:'backtest-result','walk_forward':'walk-forward-result','holdout':'holdout-comparison'}[kind]
    binary=subprocess.check_output(['node','scripts/browser_path.mjs'],text=True).strip();errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=binary,args=ARGS)
        page=browser.new_page(locale='zh-CN');page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.add_init_script('localStorage.clear();localStorage.setItem('+json.dumps(key)+','+json.dumps(task['job_id'])+');')
            page.goto('http://127.0.0.1:3000/research',wait_until='domcontentloaded')
            result=page.get_by_test_id(panel);expect(result).to_be_visible(timeout=30000)
            verification=result.get_by_test_id('research-verification')
            expect(verification).to_contain_text('尚未复验')
            with page.expect_response(lambda r:r.url.endswith('/verify')) as response:
                verification.get_by_role('button',name='复验冻结结果',exact=True).click()
            assert response.value.status==200,response.value.text()
            expect(verification).to_contain_text('复验通过',timeout=15000)
            # Deliberately malformed successful response must never retain prior success.
            page.route('**/api/research/jobs/*/verify',lambda route:route.fulfill(status=200,json={'consistency_verified':True}))
            verification.get_by_role('button',name='复验冻结结果',exact=True).click()
            expect(verification.get_by_role('alert')).to_contain_text('复验未通过')
            page.unroute('**/api/research/jobs/*/verify')
            verification.get_by_role('button',name='复验冻结结果',exact=True).click()
            expect(verification).to_contain_text('复验通过',timeout=15000)
            assert not errors,errors
            print('Research verification browser acceptance passed: retained job, real proxy/replay, malformed receipt rejection and retry; no data writes')
        finally:browser.close()
