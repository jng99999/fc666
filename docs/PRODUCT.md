# FC666 产品规格

当前实现说明：当前 Phase 1 工程入口、服务状态、Instrument/Candle 模型与数据库迁移已实现；下述交易产品能力仍未实现。

状态：Phase 0 设计；所有交易、数据、图表、AI 功能均为 Not Implemented。
来源：用户引用的“设置交易平台”对话中总控规格；读取内容在立即执行任务第 4 条截断，不能推断未读部分。以下是可读要求的工程化设计。

## 定位与原则
专业 Crypto Quant Workstation，覆盖 Trading Terminal、Market Data、Quant Research、Strategy Lab、Backtest、Replay、Paper、Portfolio、Risk、AI、Execution 和后续 Live/Optimization。
Correctness > Capital Safety > Reliability > Data Integrity > Maintainability > Performance > UX > Features。
不伪造行情、收益、账户、AI 结论或连接状态；未实现功能明确显示 Not Implemented。

## 用户流程
1. 研究者选择交易所、标准化合约、周期、数据范围，查看缺口与数据来源，下载/导入真实历史行情。
2. 在 Strategy Lab 选择版本化策略、参数、初始资金、杠杆、费率和滑点，提交后台回测任务；查看进度、错误、指标、权益曲线及图上成交。
3. 用 Replay 的 Play/Pause/Step 和 1X/5X/10X/50X 重放相同数据与策略，定位信号原因。
4. 数据健康后进入 Paper；订单、费用、成交与仓位明确标为模拟，行情仍来自真实交易所。
5. 通过 Risk Center 检查账户与相关敞口、熔断原因；全局 Kill Switch 停止新订单，平仓需要额外确认。
6. Copilot 引用有时间戳的真实系统数据解释行情或交易；缺数据时明确拒绝作结论。
7. Live 只在执行、风控、对账、恢复和验收全部通过后独立授权启用。

## 首个垂直切片
先实现 Binance Spot 公共只读行情，BTC/USDT、ETH/USDT，1m/5m/15m/1h/4h/1d；支持 OHLCV、Ticker、Trades、OrderBook。这只是首个交付范围，不代表取消期货、Funding、OI、Mark/Index Price、Basis：它们保留在后续 derivatives 子阶段。Spot 上不显示虚构 Funding/OI，不允许做空或杠杆。
先做一个适配器；OKX、Bybit 在首个适配器恢复测试通过后加入。若目标地域无法访问 Binance，记录访问证据，调整首个交易所，不绕过地域限制。

## 页面信息架构
Terminal、Market Center、Strategy Lab、Backtest Center、Replay、Paper、Portfolio、Risk Center、Copilot、Exchange Manager、Data Manager、Alerts、Logs、Settings。Live 与 Optimization 初期只列为 Not Implemented，不出现可操作下单入口。

## 验收与追踪
详细功能清单见原始规格的模块要求与 [路线图](ROADMAP.md)，数据模型见 [DATA_MODEL](DATA_MODEL.md)。每个阶段必须交付需求、设计、实现、有效测试、问题修复、审查、安全/性能检查和完成报告；跳过测试不等于通过。
