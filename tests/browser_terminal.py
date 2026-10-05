"""Real-market browser functional checks; owns only the worker stop/restart."""
import json
from pathlib import Path
import subprocess
from playwright.sync_api import sync_playwright,expect
from scripts.dev_services import stop

ROOT=Path(__file__).resolve().parents[1]
ARGS=["--no-sandbox","--disable-dev-shm-usage","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"]

METRICS="""(() => {
  window.__terminalMetrics={frames:[],paintLatencies:[],longTasks:[],lastFrame:null,seen:new Set()};
  const m=window.__terminalMetrics;
  new PerformanceObserver(list=>{for(const entry of list.getEntries())m.longTasks.push(entry.duration)}).observe({type:'longtask',buffered:true});
  new MutationObserver(()=>{
    const book=document.querySelector('.book-panel');
    if(!book?.dataset.receivedAt)return;
    const key=book.dataset.generation+':'+book.dataset.sequence;
    if(m.seen.has(key))return;m.seen.add(key);
    const received=Date.parse(book.dataset.receivedAt);
    requestAnimationFrame(()=>{const delay=Date.now()-received;if(Number.isFinite(delay)&&delay>=0)m.paintLatencies.push(delay);});
  }).observe(document,{subtree:true,childList:true,attributes:true});
  function frame(now){if(m.lastFrame!==null)m.frames.push(now-m.lastFrame);m.lastFrame=now;requestAnimationFrame(frame)}
  requestAnimationFrame(frame);
})()"""

if __name__=="__main__":
    executable=subprocess.check_output(["node","scripts/browser_path.mjs"],cwd=ROOT,text=True).strip()
    errors=[];results=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=executable,args=ARGS,headless=True)
        context=browser.new_context(viewport={"width":1440,"height":1000},locale="zh-CN")
        page=context.new_page();page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto("http://127.0.0.1:3000",wait_until="domcontentloaded")
        expect(page.get_by_test_id("live-price")).not_to_have_text("—",timeout=30000)
        expect(page.locator('[data-testid="orderbook-bids"] .book-level')).to_have_count(10,timeout=30000)
        expect(page.locator('[data-testid="recent-trades"] tr')).to_have_count(15,timeout=30000)
        page.wait_for_function("document.querySelector('.chart-caption').textContent.match(/[1-9][0-9]+ 根/)")
        pixels=page.get_by_test_id("price-chart").evaluate("""element=>{
            let colored=0;for(const canvas of element.querySelectorAll('canvas')){
              const ctx=canvas.getContext('2d');if(!ctx)continue;
              const p=ctx.getImageData(0,0,canvas.width,canvas.height).data;
              for(let i=0;i<p.length;i+=4)if((p[i]===53&&p[i+1]===208&&p[i+2]===160)||(p[i]===255&&p[i+1]===119&&p[i+2]===133))colored++;
            }return colored;
        }""")
        assert pixels>20,"Candlestick colors must be drawn, not a blank canvas"
        with page.expect_response(lambda response:'/api/market/indicators' in response.url) as indicator_response:
            page.get_by_role('button',name='指标 overlays').click()
        data=indicator_response.value.json()
        assert indicator_response.value.status==200 and data['closed_only'] and len(data['rows'])>=120
        assert data['rows'][-1]['values']['ema'] is not None
        expect(page.locator('.chart-caption')).to_contain_text('SMA20')
        expect(page.get_by_role('button',name='指标 overlays')).to_have_attribute('aria-pressed','true')
        page.wait_for_function("""(()=>{const element=document.querySelector('[data-testid=price-chart]');let found=0;for(const canvas of element.querySelectorAll('canvas')){const ctx=canvas.getContext('2d');if(!ctx)continue;const p=ctx.getImageData(0,0,canvas.width,canvas.height).data;for(let i=0;i<p.length;i+=4)if(p[i]===245&&p[i+1]===201&&p[i+2]===106)found++;}return found>20;})()""")
        page.get_by_role('button',name='指标 overlays').click()
        expect(page.get_by_role('button',name='指标 overlays')).to_have_attribute('aria-pressed','false')
        results.append("real indicators API and SMA overlay drawn/toggled")
        results.append("real ticker/book/trades/candles rendered")
        for interval in ["5m","15m","1h","4h","1d"]:
            with page.expect_response(lambda response:f"timeframe={interval}" in response.url and '/api/market/candles' in response.url) as response:
                page.get_by_role("button",name=interval,exact=True).click()
            candles=response.value.json();assert len(candles)>=120 and all(bar['is_closed'] for bar in candles)
            expect(page.get_by_role("button",name=interval,exact=True)).to_have_attribute('aria-pressed','true')
        page.get_by_label("交易对",exact=True).select_option("ETHUSDT")
        expect(page.locator('.ticker-head strong')).to_have_text('ETH/USDT')
        expect(page.get_by_test_id('live-price')).not_to_have_text('—',timeout=15000)
        results.append("symbol and all six interval data paths")
        separator=page.get_by_role('separator',name='调整自选面板宽度')
        separator.focus();separator.press('ArrowRight');expect(separator).to_have_attribute('aria-valuenow','210')
        box=separator.bounding_box();assert box
        page.mouse.move(box['x']+box['width']/2,box['y']+100);page.mouse.down();page.mouse.move(box['x']+40,box['y']+100,steps=8);page.mouse.up()
        width=separator.get_attribute('aria-valuenow');assert int(width)>230
        page.get_by_role('button',name='深度 / 成交',exact=True).click();expect(page.locator('.depth-panel')).not_to_be_visible()
        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_label('交易对',exact=True)).to_have_value('ETHUSDT')
        expect(page.get_by_role('button',name='1d',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.get_by_role('separator',name='调整自选面板宽度')).to_have_attribute('aria-valuenow',width)
        expect(page.locator('.depth-panel')).not_to_be_visible()
        page.get_by_role('button',name='深度 / 成交',exact=True).click()
        page.get_by_role('button',name='图表全屏').click();page.wait_for_function('document.fullscreenElement !== null');page.keyboard.press('Escape');page.wait_for_function('document.fullscreenElement === null')
        page.get_by_role('tab',name='持仓',exact=True).focus();page.keyboard.press('ArrowRight');expect(page.get_by_role('tab',name='订单',exact=True)).to_have_attribute('aria-selected','true')
        results.append("pointer/keyboard resize, persisted workspace, fullscreen, keyboard tabs")
        try:
            stop('market')
            expect(page.get_by_test_id('live-price')).to_have_text('—',timeout=15000)
            expect(page.locator('[data-testid="orderbook-bids"]')).to_have_count(0)
            assert page.locator('.chart-caption').inner_text().find('根历史')>=0
            results.append("worker loss hides stale live data while history remains")
        finally:subprocess.run([str(ROOT/'.venv/bin/python'),'scripts/dev_services.py','start'],cwd=ROOT,check=True)
        expect(page.locator('[data-testid="orderbook-bids"] .book-level')).to_have_count(10,timeout=30000)
        expect(page.get_by_test_id('live-price')).not_to_have_text('—',timeout=15000)
        results.append("worker recovery restores live view")
        page.evaluate(METRICS)
        page.wait_for_function('window.__terminalMetrics.frames.length >= 1800',timeout=45000)
        metrics=page.evaluate("""() => {const m=window.__terminalMetrics;function percentile(a,p){if(!a.length)return null;const s=[...a].sort((a,b)=>a-b);return s[Math.floor((s.length-1)*p)]}return {frameSamples:m.frames.length,frameP95Ms:percentile(m.frames,.95),paintSamples:m.paintLatencies.length,paintP95Ms:percentile(m.paintLatencies,.95),paintMaxMs:Math.max(...m.paintLatencies),longTasks:m.longTasks};}""")
        assert metrics['paintSamples']>=50,metrics
        assert metrics['paintP95Ms']<100,metrics
        page.screenshot(path=str(ROOT/'.runtime/terminal-desktop.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        mobile=browser.new_context(viewport={'width':393,'height':852},locale='zh-CN',is_mobile=True,has_touch=True)
        phone=mobile.new_page();phone.on('pageerror',lambda error:errors.append(str(error)))
        phone.goto('http://127.0.0.1:3000',wait_until='domcontentloaded')
        expect(phone.get_by_test_id('live-price')).not_to_have_text('—',timeout=30000)
        phone.get_by_role('button',name='订单簿',exact=True).click();expect(phone.locator('.book-panel')).to_be_visible();expect(phone.locator('.chart-panel')).not_to_be_visible()
        phone.get_by_label('移动端交易对').select_option('ETHUSDT');expect(phone.locator('.ticker-head strong')).to_have_text('ETH/USDT')
        phone.get_by_role('button',name='成交',exact=True).click();expect(phone.locator('.trades-panel')).to_be_visible()
        phone.get_by_role('button',name='图表',exact=True).click();expect(phone.locator('.chart-panel')).to_be_visible()
        assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
        phone.screenshot(path=str(ROOT/'.runtime/terminal-mobile.png'),full_page=True)
        results.append("mobile chart/book/trades and no horizontal overflow")
        assert not errors,errors
        report={'browser':browser.version,'passed':results,'browser_errors':errors,'metrics':metrics}
        (ROOT/'.runtime/browser-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False));print(json.dumps(report,indent=2,ensure_ascii=False))
        browser.close()
