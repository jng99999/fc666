# 分阶段路线与当前状态

状态：Phase 0 PARTIAL（架构/产品/设计文档已落地；研究深度与竞品 UI 实测待补充）。Phase 1 工程基础已实现并验证（见 [PHASE_1_REPORT](PHASE_1_REPORT.md)）；Phase 2 Spot 与 Phase 3 核心只读终端已实现并验证（见 PHASE_2_REPORT/PHASE_3_REPORT）；Phase 2b 衍生品、Phase 3 overlays/长时性能仍待实现或验收；Phase 4 首批基础指标/确认swing/价格overlays已实现，完整结构与regime仍待完成（见PHASE_4_REPORT）；Phase 5 Spot回测首片已实现（见PHASE_5_REPORT），其余回测能力未完成；Phase6后台任务首片已实现（见PHASE_6_REPORT），完整SDK/Lab待完成；Phase7闭合柱回放与EMA/SMA决策已实现；Phase8历史Spot Paper首片已实现，实时Paper与Phase9–15待完成。
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
