# 参考研究记录

读取日期：2026-10-05。范围：官方 README、Freqtrade lookahead-analysis 文档、Jesse Strategy 源码开头、Hummingbot order_book_tracker 源码开头、Binance WS 官方文档开头。分支会变化；未固定 commit，未安装运行这些项目，不能据此宣称完成完整代码审计。仅借鉴设计思想，不复制代码。

| 来源 | 已观察内容 | FC666 决策 / 避免 |
|---|---|---|
| [Freqtrade README](https://github.com/freqtrade/freqtrade/blob/develop/README.md) | dry-run、backtest、optimization、WebUI、lookahead-analysis 命令 | Paper 优先，研究/运行分开；不拿 backtest 高收益作实盘 readiness |
| [Lookahead analysis](https://github.com/freqtrade/freqtrade/blob/develop/docs/lookahead-analysis.md) | full dataframe 可泄漏；切片对照；未触发信号不算验证 | as_of/available_at 合同、后缀扰动测试，禁止全历史均值和未来 shift |
| [Jesse README](https://github.com/jesse-ai/jesse/blob/master/README.md) / [Strategy](https://github.com/jesse-ai/jesse/blob/master/jesse/strategies/Strategy.py) | 同框架研究/多周期/图表；Strategy 持有 broker/position/chart/ML 状态 | 保留 typed 策略与图表归因，FC666 将 broker/secret 与 strategy 隔离；不照搬继承巨型对象 |
| [Hummingbot README](https://github.com/hummingbot/hummingbot/blob/master/README.md) / [Book tracker](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/core/data_type/order_book_tracker.py) | connector 标准化 REST/WS，Controllers/Executors 分工；tracker 有 diff/snapshot/reject/latency metrics | adapter 与生命周期拆分，订单簿恢复与观测单独验收 |
| [QuantsLab README](https://github.com/hummingbot/quants-lab/blob/main/README.md) | core/app 分层，任务调度、数据/feature/backtest/controller | research worker 与 API 隔离；不因为参考项目使用 MongoDB 就新增数据库 |
| [Condor README](https://github.com/hummingbot/condor/blob/main/README.md) | AI/用户界面经 Hummingbot API 控制执行；强调权限与私有访问 | 借鉴 API 边界；FC666 不开放 AI 下单工具，不引入 Telegram 或新 VPN 作为默认依赖 |
| [Lightweight Charts README](https://github.com/tradingview/lightweight-charts/blob/master/README.md) | chart plugins、独立 series、NOTICE 与归属要求 | ChartAdapter 封装、局部 imperative 更新、保留归属链接；绘图性能需自己测量 |
| [Binance WS](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) | 连接寿命/heartbeat/订阅限速，原始时间单位声明 | adapter 管理 heartbeat、重连和订阅恢复，时间单位显式转换 |

## 尚未完成的研究
- 参考项目完整 execution/OMS、回测 matcher、reconciliation、optimization/ML 实现与版本固定；当前不能称“深入拆解已完成”。
- Binance snapshot+delta 的完整序列算法、OKX/Bybit 差异；实现前按当时官方协议逐项验证。
- TradingView/Binance/OKX/Bybit 的真实终端截图、resize/keyboard/workspace 与订单确认行为比较；当前 UI 线框不是竞品实测成果。
这些是 Phase 0 剩余研究，不阻止保存已明确设计，但阻止宣称整个 Phase 0 Done。

## 补充源码核查

- [Freqtrade Backtesting](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/optimize/backtesting.py)：读取 futures funding/mark 数据加载、_get_close_rate/_get_close_rate_for_stoploss；存在同 bar trailing 的悲观价格路径。FC666 必须保存 bar 内撮合假设，缺 funding 不能按免费持仓处理。
- [Freqtrade Bot](https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/freqtradebot.py)：startup_update_open_orders 在启动时从持久化订单更新状态；funding 更新和交易所 capability 验证显式执行。FC666 保留恢复 gate，并将日/周损失边界固定 UTC，避免调度器本地时区歧义。
- [Hummingbot Position Executor](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy_v2/executors/position_executor/position_executor.py)：control_task 区分 RUNNING/SHUTTING_DOWN；关闭过程中等待订单完成并请求 connector 更新。FC666 不能把“请求停止”报成“已平仓”，未知订单需要查询和审计。
- Binance 官方 snapshot+delta 算法全文已读：先订阅并缓冲，再取 snapshot；按 U/u/lastUpdateId 选择衔接事件，丢弃已覆盖事件，U > local_id + 1 时重建；quantity=0 删除价位，其他 delta 是新数量而非加量。snapshot 最多每侧 5000 档，未变化的远端价位可能未知。实现验收必须覆盖这种深度边界，不能将局部簿宣称完整流动性。

## 竞品访问结果与阻碍

本次 HTTPS 只读访问 www.tradingview.com/chart/、www.binance.com/en/trade/BTC_USDT、www.okx.com/trade-spot/btc-usdt、www.bybit.com/trade/spot/BTC/USDT 均收到代理 Tunnel 403 Forbidden。当前无浏览器交互工具；未观察实际界面，不能验证拖拽、键盘与响应性能。
已提交四个精确 www 域名的网络草稿用于研究，保留 package_managers 预设。草稿不自动应用到当前机器，需在环境设置保存后重试；若页面动态渲染仍无法读取，需要支持浏览器的任务环境或用户提供的界面材料。其余源码完整审计是后续阶段按具体实现开展，不应在 Phase 0 伪称已完成。

## 网络问题复验与解决（2026-10-05）

先核对配置：草稿已持久化四个 www 域名；runtime status 的 network.state 为 unknown，不能单靠该状态宣称配置生效。实际请求证明 TradingView/Binance/Bybit 返回 HTTPS 200；raw.githubusercontent.com 亦为 200。
OKX 默认 Python HTTP 请求返回网站 HTTP 403（不再是 CONNECT tunnel 拒绝）；采用普通浏览器 User-Agent、保留原代理与 TLS 验证后，首页与 BTC/USDT spot 页面均返回 200。未使用绕过代理、关闭 TLS、VPN 或账户凭据。网页标题与静态内容已读取；OKX 内容显示 United States 页面，不能据此推断完整产品/交易权限。
本轮四个终端页面都能获取响应，之前“必须先修改网络才能继续研究”的阻碍已解除。HTML 证据范围：TradingView 为图表入口，Bybit 为 Spot Trading Platform，OKX spot 文本包含 Limit/Market/Buy/Sell/Chart 标签；Binance 页面响应未匹配这些 UI 标签，不能当成成功渲染终端。
HTTP 200 不等于浏览器界面或市场数据可用。当前无浏览器交互工具，拖拽/键盘/响应性能仍未实测；保留为设计验证与 Phase 3 实现检查项，不把它当成整体仓库开发或环境安装阻碍。真实行情 API 与 WebSocket 连接在 Phase 2 单独申请所需具体目的域名并验证，不与官网访问混淆。
