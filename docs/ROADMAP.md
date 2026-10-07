# 分阶段路线与当前状态

状态：Phase 0 PARTIAL（架构/产品/设计文档已落地；研究深度与竞品 UI 实测待补充）。Phase 1 工程基础已实现并验证（见 [PHASE_1_REPORT](PHASE_1_REPORT.md)）；Phase 2 Spot 与 Phase 3 核心只读终端已实现并验证（见 PHASE_2_REPORT/PHASE_3_REPORT）；Phase 2b 衍生品、Phase 3 overlays/长时性能仍待实现或验收；Phase 4 首批基础指标/确认swing/价格overlays已实现，完整结构与regime仍待完成（见PHASE_4_REPORT）；Phase 5 Spot回测首片已实现（见PHASE_5_REPORT），其余回测能力未完成；Phase6后台任务首片已实现（见PHASE_6_REPORT），完整SDK/Lab待完成；Phase7闭合柱回放与EMA/SMA决策已实现；Phase8历史Spot Paper、有界实时闭合柱Paper及账户列表/控制记录已实现；Phase9独立实时Paper场景估值首片已实现；完整Paper/OMS、组合风险及Phase9剩余/10–15待完成。
禁止一次性实现完整项目；每阶段执行需求、架构检查、设计、实现、测试、运行、修复、审查、安全、性能、文档、报告。阶段间依赖可以先设计或引入必要基础，但不得伪称后续完整模块已完成。

| Phase | 交付范围 | 阶段验收 |
|---|---|---|
| 0 | Architecture/Product/Design System、参考研究 | 关键合同/边界完整、研究有证据、竞品交互比较、文档一致；当前竞品研究未完成 |
| 1 | Foundation、DB、Core Models | 锁文件安装、Compose/native 支持验证、迁移 up/down、Decimal/UTC/唯一约束、API readiness；保存已测试 setup/start 指令 |
| 2 | Public adapter、Market Data、WS | 两币种六周期真实历史/实时行情；重复/乱序/gap/重连恢复；Phase 2b 加 derivatives 数据及能力验收 |
| 3 | Terminal、Chart、Realtime UI | 实际数据联动、空/错/stale 状态、resize/keyboard/persistence、图表 overlays、UI benchmark |
| 4 | Indicators、Market Structure | 全部指定指标、regime/structure、无未来泄漏与 warmup 测试 |
| 5 | Vectorized/Event Backtest | 一致可复现、撮合/fee/slippage/funding、spot/futures capability、未来泄漏、分析与图上成交 |
| 6 | Strategy SDK、Strategy Lab | 共用模式合同、版本化策略、异步任务、参数验证、完整结果与交易详情 |
| 7 | Replay | Play/Pause/Step/speeds、虚拟时间、seek 重放、所有消费者 as_of 一致 |
| 8 | Paper | 真实行情驱动、模拟订单/fill/fee/funding/PnL；最小 Risk veto 与 Kill Switch 先作为依赖实现 |
| 9 | Portfolio、完整 Risk | Open/Add/Reduce/Close/Reverse、equity/exposure、组合相关性、全部限额与熔断 |
| 10 | AI/Copilot | 真实证据引用、schema、权限/密钥隔离、stale 拒答、历史时点约束 |
| 11 | Execution/OMS | 完整状态机、幂等、提交未知态、cancel race；仅 sandbox/paper 验收 |
| 12 | Reconciliation/Recovery | 启动/断线状态一致、超时已成交、restart、mismatch halt、审计 |
| 13 | Live | 11/12、Risk、integration、recovery、chaos 全通过；另行实盘授权；默认仍关闭 |
| 14 | Optimization | Grid/Random/Bayesian、walk-forward、OOS、Monte Carlo、overfit 检测 |
| 15 | Hardening | 可复现负载/性能、安全、chaos、备份恢复、上线 runbook |

SDK 的最小 typed port 在 Phase 5 为依赖提供，Phase 6 才完成用户扩展工具；同理 Phase 8 不能等到 Phase 9 才有基本风控。任何延期必须保留原需求与 outstanding 状态。
Phase 0 当前验证：仓库最初仅 README；Python 3.12.14、Node 24.19.0 存在（兼容性未验收）；文档检查结果见 [PHASE_0_REPORT](PHASE_0_REPORT.md)。后续继续补足研究；当前工程运行兼容性与验证记录见 Phase 1–3 报告。

当前执行目标与范围见 [DELIVERY_PLAN](DELIVERY_PLAN.md)，已测试开发指令见 [DEVELOPMENT](DEVELOPMENT.md)。Phase 0 竞品交互研究仍待补充，不阻塞独立工程建设。

Phase9持久场景与估值历史：不可变账户选择/Decimal提示阈值，UUID幂等创建及保存，完整报告与输入原子持久化、数据库防修改、分页查询、刷新和丢响应后的原请求重试、完整导出与离线复算；schema0009。见PORTFOLIO_HISTORY.md。下一目标明确历史快照可比性、缺失区间及连续性，再实现组合历史分析；共享资金、完整Risk/OMS与Live保持未实现。

Phase9历史可比性切片：最近最多8个持久快照，冻结输入离线重算，相邻分钟/读取时钟/账户定义/版本/观察和行情前缀/worker健康检查，缺口明确分段，区间内Decimal权益差，无插值、跨缺口累计或连续回撤。见PORTFOLIO_CONTINUITY.md；多阶段目标见AUTONOMOUS_EXECUTION_PLAN.md。下一目标先定义分段采样权益展示及观察点指标，再推进版本化组合风险可见性和Paper执行恢复；Live关闭。

Phase9分段采样权益：独立sampled-v1接口/导出，沿用continuity-v1区间边界，Decimal观察点回撤金额/比例与各自峰谷编号，单点/零峰值明确缺失，SVG按区间断开、精确表格/焦点详情、完整离线复算。见PORTFOLIO_SAMPLED.md。下一目标冻结版本化组合风险提示策略与历史可见性，保持只读监控边界，再推进Paper执行恢复；实盘关闭。

Phase9只读风险历史：版本化冻结gross/BTC/ETH敞口提示策略及哈希，UNKNOWN/HINTS_PRESENT/NO_CONFIGURED_HINTS区分，原始worker/停止买入提示与报价账本时钟，相邻可比观察才记录阈值进入/退出，缺口重置不假称风险解除，完整导出与离线复算。见PORTFOLIO_RISK_HISTORY.md。下一目标为Paper执行和恢复基础：先定义订单意图、幂等、丢确认与重启对账验收边界，再实现故障测试；交易执行门控、完整Risk/OMS、共享资金、认证和Live仍未完成。

Paper执行恢复基础首片：只读repeatable-read账本/来源/规则/worker检查，稳定模拟订单及成交关联回执、暂停/终止/阻断状态保留、来源不一致需核对；完整离线核对。并行故障验收覆盖提交后丢确认、SQL flush后提交前回滚、重建连接与并发重复接受。见PAPER_RECOVERY.md。下一目标先定义独立订单意图/幂等键和状态机，再实施持久模拟执行日志；现有公式与账户保持兼容，不自动修复或补单，Live关闭。

Paper 独立完成态订单意图切片：schema0010，账户命名空间确定性ID，原账本和不可变意图同事务保存，保留 FILLED/PARTIAL_CANCELLED/REJECTED 路径，旧账户显式未物化覆盖、只读核对和离线导出。下一目标是执行前独立提交与恢复的待执行意图；实盘 OMS、共享资金与 Live 仍未实现。见 PAPER_ORDER_INTENTS.md。

执行前准备切片：schema0011，闭合柱批次 PREPARED 独立提交，消费事务复核原账户、行情和时限；两阶段中断、控制取消、来源阻断、幂等成交验证。每账户最多1000条不可删除准备历史；GET与页面只读。准备批次不代表确定数量的交易所订单。下一目标是每订单明确执行前授权与账户执行门禁。见 PAPER_PREPARATION.md。

逐单Paper规则授权切片：schema0012，预计订单/数量/价格/费用与风险决定在准备事务不可变落盘；消费前重算完整计划，消费后对比实际效果，方向门禁保持halt买拒卖可。旧准备无事前证据明确取消或显示不可用；只读页核对最新20批次，不删除旧历史。交易所OMS、身份鉴权、共享资金与对账仍未实现。见 PAPER_AUTHORIZATION.md。


## Local Paper lifecycle — schema 0013

Independent immutable CREATED evidence commits with preparation before economic acceptance. FILL/OUTCOME and accepted receipts commit atomically; source, expiry and control cancellations preserve zero-fill outcomes. Read docs/PAPER_LIFECYCLE.md and docs/PHASE_9_LIFECYCLE_REPORT.md. The read-only lifecycle endpoint/widget verifies latest20 complete batches, explicit older/legacy coverage and cumulative quantity, fees and notional. Production emits one local fill per authorization (sequences0..2); pure reducer split-fill fixtures do not establish private exchange integration. Legacy pending enrollment precedes consumption in a separate transaction; terminal history is never retroactively invented.

Current head0013 supersedes earlier schema notes. Run bash scripts/start.sh to preserve .env/volumes and restore all five services. Stop owned services before bash scripts/check.sh. No new dependencies, credentials, hosts or services; Live stays disabled. Next goal: a Paper-only fault adapter and explicit private-style fill/cancel reconciliation contract before exchange integration.


## Production projected funding — schema0014

Read PAPER_FUNDING.md and PHASE_9_FUNDING_REPORT.md. New preparation commits account-specific projected-fill cash/inventory holds before economic acceptance. Original ledger, exact funded settlement, lifecycle and preparation outcome commit atomically; cancellation releases a hold without adding money. Legacy pending coverage stays unavailable with null available/reserved resources. GET funding and realtime widget are read-only, latest20 batches with older scope. Run scripts/start.sh for current head0014 and all five services; no dependency/secret/host additions. The SQLite leased submission laboratory remains separate; full requested quantity, asynchronous fills and shared-capital risk still require a new engine contract. Live remains disabled.
