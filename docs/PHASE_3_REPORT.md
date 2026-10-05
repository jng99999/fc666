# Phase 3 核心只读终端验收报告

日期：2026-10-05。核心终端已实现；完整 Phase 3 仍 PARTIAL，指标/成交 overlays、深度历史导航性能及持续负载测试保留待办。

## 已实现
Binance Spot BTC/USDT、ETH/USDT 真实报价、六周期 K 线与 volume、订单簿和成交。ChartAdapter 隔离图表库；Decimal 保留在模型，绘图边界转换 Number。Zod 验证 REST/WS 数据，Zustand 局部订阅，TanStack Query 管理历史。WS latest_snapshot 用于 UI，不是持久策略事件总线。
布局支持拖拽/键盘 resize、localStorage 持久化、显隐、全屏、底部键盘 tabs 和移动端 chart/book/trades。断线隐藏实时值，保留历史；行情恢复后重建实时视图。未实现交易能力明确展示 Not Implemented。

## 验证结果
- 后端 52 tests passed；Alembic 无模型差异；前端类型检查和生产构建通过。上游 Starlette/httpx 弃用 warning 保留可见。
- Chromium 153.0.8010.0 真浏览器无页面 JS 错误；真实 K 线绘制、六周期数据、交易对切换、拖拽/键盘 resize、保存重载、全屏退出、键盘 tabs、worker 故障/恢复、393px 移动端均通过。
- 约 30 秒、1800 个 frame samples：帧间隔 P95 16.8ms；301 个订单簿更新样本，从 worker received_at 到下一 RAF 的 P95 64ms，最大 67ms；无观测到的 long tasks。包含本机网关传输和 DOM 更新，不包含交易所到 worker 的网络耗时；RAF 指标不是显示器实际扫描测量。
- 隔离合成 benchmark：保留 100000 根历史、绘制窗口 5000 根，setHistory 18.2ms；100 次 last-bar update P95 0.4ms，max 1.2ms。优化前全量绘制 233.3ms。数据只用于 benchmark，不进入产品。
- 本机 API 100 请求、并发 4、每次 120 根：P50 25.54ms、P95 55.28ms、max 78.43ms。

## 可复现证据与边界
运行 tests.browser_terminal；浏览器输出 .runtime/browser-report.json、terminal-desktop.png、terminal-mobile.png。图表 benchmark 源码 tests/web/chart-benchmark.ts，结果 .runtime/chart-windowed.json；API 测量 .runtime/api-benchmark.json。运行文件被忽略，不提交为产品数据。
测试使用 npm 完整性验证安装的 @sparticuz/chromium 和软件图形渲染；未关闭 TLS 或 web security。标准 Playwright CDN 下载受代理限制，已用 npm 浏览器分发解决。
这不是十分钟持续负载、生产并发或全市场性能验收。React commit 数、深度历史 panning、完整 accessibility audit 尚未量化。未实现指标、回测成交 overlays、衍生品、策略、订单、模拟/实盘交易及 AI。
TradingView 官方 NOTICE 与外链、库 attribution logo 已保留，见 docs/THIRD_PARTY_NOTICES.md。
