# 验证计划

当前实现说明：Phase 1 当前已有 22 项模型/真实数据库/Redis/API 测试并全部通过；下面其他测试层为后续模块计划。

状态：设计，应用测试 Not Implemented；Phase 0 文档检查不能证明交易系统可用。

| 层级 | 必须发现的失效 |
|---|---|
| Unit | Decimal/rounding、instrument 映射、candle 校验、状态机非法迁移、费用/PnL |
| Integration | PostgreSQL migration/constraints、Redis 分发、adapter 合同、HTTP/WS 授权 |
| Regression | 已修复错误、版本化真实数据 manifest 的稳定输出 |
| Simulation | partial/reject/cancel race、SL/TP ambiguity、funding、资金守恒 |
| Recovery | WS disconnect/gap/resync、exchange timeout、restart、对账 mismatch |
| Chaos | 丢包、乱序、重复、数据库不可用、进程崩溃、双执行者 fencing |
| Performance | 100k bars、UI commits/frame time、API p95、研究 worker 隔离 |

未来数据泄漏测试修改数据 T 之后的后缀，比较 T 之前所有 indicators/signals/orders；用测试含未来读入的反例确认测试确实失败，不能只跑无交易策略。多周期 closed-bar 与 available_at 单独覆盖。
交易测试证明重复 intent/fill 不重下单/重计 PnL；超时已成交后恢复不重试 create；风险限额并发 reservation；Kill Switch 重启后仍关闭；断线期间订单成交能对账；无法对账禁止启用。
合成数据只用于标明的单元/故障测试，不能在 UI 冒充真实市场。端到端 smoke 使用真实公共数据，记录来源和时段；网络拒绝为 blocker，不改成固定价格假接口通过。
每次报告记录 command/target/版本/数据/exit status、passed/failed/skipped/unrun。零测试不验收；失败先定位 setup vs repository defect，禁止删除断言。Phase 0 只检查文档链接、覆盖与 diff，安全/性能为设计审查，实际运行验收在对应阶段。
