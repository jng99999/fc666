# 执行计划与目标

按可验证的阶段交付，不设无依据的完工日期。以正确性、资金安全、可靠性和数据完整性为优先。

## 已交付与验证
- Phase 1：工程、类型与数据库约束、真实依赖 readiness、冻结安装和可复用启动脚本。
- Phase 2 Spot：BTC/USDT、ETH/USDT 六周期历史/实时行情、序列校验订单簿、幂等存储、失效清理和重连补数。衍生品数据保留 Phase 2b 待办。
- Phase 3 核心终端：真实 chart/watchlist/book/trades、局部订阅、布局保存、键盘/拖拽 resize、全屏、移动端、断线恢复。浏览器和短时性能验证通过，详见报告。

## 当前执行目标：Phase 4 指标与市场结构
首批SMA/EMA/RSI/ATR/MACD/Bollinger/VWAP、确认swing、真实API与价格overlays已实现并验证，见 PHASE_4_REPORT.md 与 INDICATORS.md。已补充独立指标pane、结构确认/突破定义、参数与窗口交互；已增加ER/ATR可解释分类；完整多周期/可校准regime仍待实现；继续用独立参考和因果性验证，不把基础均线关系叫作完整regime。
验收：明确未成熟状态、无未来数据泄漏、批量与逐根一致、参数边界和实际行情验证；未实现指标不能返回伪成功。

## Phase 5 首片已交付
Spot EMA Long/Flat回测、Decimal账本、真实数据API/研究页面、完整导出/离线复现已验证，见PHASE_5_REPORT.md。已追加v2成本/完整交易/风险收益分析及旧版本复现，见PHASE_5_ANALYTICS_REPORT.md。下一切片完善撮合语义与事件/向量对照；其余回测能力保留未实现。自主执行与发布边界见LONG_TASK_PLAN.md。

## Phase 6 首片已交付
固定版本策略catalog、PG后台研究任务、进度/取消/失败、快照/lease恢复、网页刷新及结果持久化已验证，见PHASE_6_REPORT.md。下一切片为策略扩展与更多撮合/独立对照；完整SDK/Lab仍未完成。

## 保留待办与后续阶段
Phase 0 深入竞品交互研究、Phase 2b derivatives、Phase 3 指标/成交 overlays 与长时负载/深度导航性能验收继续保留，不将短时 smoke 宣称完整性能达标。
Phase 5–15 按 ROADMAP.md：回测 → SDK/Lab → Replay → Paper → Portfolio/Risk → AI → OMS → Reconciliation → Live → Optimization → Hardening。
Live 默认关闭，必须满足风险、执行、恢复、故障验收并获得独立实盘授权；健康接口和纸面收益不能解锁下单。
