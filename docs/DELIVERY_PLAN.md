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

Phase 6 策略扩展基础已完成：见STRATEGY_REGISTRY.md。下一执行顺序：v2历史复现与旧队列兼容 → v3策略/参数快照 → SMA回测与页面 → 后端、真实任务与浏览器验收。SMA当前只完成决策模块，未开放任务提交。

Phase 6 两种内置策略执行已完成：EMA/SMA可在研究页面提交，v3运行参数进入快照/manifest，v2旧任务及v1/v2导出兼容验证通过。详见STRATEGY_REGISTRY.md。下一目标：有界参数批次研究和真实结果比较；完整SDK、代码沙箱与完整撮合对照继续保留待办。

Phase 6 参数批次研究已实现：最多8组同历史/规则/截止快照，整批原子提交，逐项失败/取消隔离，真实指标比较与完整导出。见RESEARCH_BATCHES.md及PHASE_6_BATCHES_REPORT.md。下一目标为明确数据隔离的样本内/样本外分段研究，仍不自动寻优或开放实盘。

Phase 6 固定参数分段研究已实现：独立样本区间、冷启动资金/指标/信号，结果分别计算，不自动选参。见HOLDOUT_RESEARCH.md及PHASE_6_HOLDOUT_REPORT.md。下一目标为Phase7历史市场回放首片；完整SDK/代码沙箱/自动优化/滚动再训练仍保留待办。

Phase7闭合柱回放首片已实现：固定PG快照、prefix-only API、时钟与CAS游标、播放控制/刷新恢复/因果指标，见REPLAY.md与PHASE_7_REPORT.md。下一目标为可审计回放策略决策事件；完整逐笔回放/撮合、Paper账户与Risk仍未实现。

Phase7回放决策已完成：冻结EMA/SMA参数、闭合柱目标事件、前缀下载与离线核对，兼容旧v1。163项后端、迁移、类型/构建及两套回放浏览器验证通过，见PHASE_7_DECISIONS_REPORT.md。下一目标：Phase8有限Spot Paper账户与最低风险限制，明确资金/库存、下一柱撮合、费用规则与恢复语义后实现；实盘关闭。

Phase8历史Paper首片：独立Decimal资金/库存、下一柱开盘模拟成交、费用/滑点/规则、买入金额/持仓/回撤限制、停止买入、PG原子账本与CAS恢复、网页和前缀下载/核对。范围见PAPER_TRADING.md；验收见PHASE_8_REPORT.md。下一目标为公共实时闭合柱Paper扩展，先定义断线补数、重复/修正数据、订单状态与持久恢复，实盘保持关闭。

Phase8实时闭合柱Paper首片：创建时仅历史预热、后台接受连续真实闭合柱、及时收盘参考成交、迟到/激活前拒绝、修正/规则变更阻断、缺口等待、独立worker与持久恢复；五服务/0007 head，见REALTIME_PAPER.md与PHASE_8_REALTIME_REPORT.md。下一目标：账户列表/状态可见性与可审计控制记录，明确停止账户保留/恢复边界，再推进Portfolio与完整Risk；实盘关闭。

Phase8账户发现与控制审计：历史/实时账户状态筛选、分页、选择/刷新恢复和停止账户保留；事务内APPLIED/NOOP/CONFLICT记录，拒绝修改/删除，身份未验证。当前head0008，见PAPER_ACCOUNTS.md。下一目标先定义模拟Portfolio估值时钟与陈旧/缺失报价边界；完整Risk/OMS、用户鉴权及Live继续保留待办。

Phase9模拟场景估值首片：显式1..8个独立实时账户，PG同快照/共同闭合分钟报价、Decimal现金/市值/盈亏/按币种敞口，账本/规则/数据/报价或到账异常时不汇总；风险阈值只提示，完整输入可离线核对。见PORTFOLIO_VALUATION.md。下一目标定义并实现版本化场景与持久估值快照/历史查询；共享资金账本、完整Risk/OMS、组合回撤和Live仍未完成。

Phase9持久场景与估值历史：不可变账户选择/Decimal提示阈值，UUID幂等创建及保存，完整报告与输入原子持久化、数据库防修改、分页查询、刷新和丢响应后的原请求重试、完整导出与离线复算；schema0009。见PORTFOLIO_HISTORY.md。下一目标明确历史快照可比性、缺失区间及连续性，再实现组合历史分析；共享资金、完整Risk/OMS与Live保持未实现。

Phase9历史可比性切片：最近最多8个持久快照，冻结输入离线重算，相邻分钟/读取时钟/账户定义/版本/观察和行情前缀/worker健康检查，缺口明确分段，区间内Decimal权益差，无插值、跨缺口累计或连续回撤。见PORTFOLIO_CONTINUITY.md；多阶段目标见AUTONOMOUS_EXECUTION_PLAN.md。下一目标先定义分段采样权益展示及观察点指标，再推进版本化组合风险可见性和Paper执行恢复；Live关闭。

Phase9分段采样权益：独立sampled-v1接口/导出，沿用continuity-v1区间边界，Decimal观察点回撤金额/比例与各自峰谷编号，单点/零峰值明确缺失，SVG按区间断开、精确表格/焦点详情、完整离线复算。见PORTFOLIO_SAMPLED.md。下一目标冻结版本化组合风险提示策略与历史可见性，保持只读监控边界，再推进Paper执行恢复；实盘关闭。
