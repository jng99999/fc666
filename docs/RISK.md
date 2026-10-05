# 风控设计

状态：设计，Not Implemented；Risk 拥有最终否决权。

RiskDecision 记录 intent、approved/rejected、reason_codes、policy_version、as_of、输入快照 hash 与限制余量。未知数据或未知规则 fail closed。审批不是永久通行证，提交前重新检查 gate、余额与数据新鲜度。
独立检查 Max Position Size/Risk、Account Risk、Daily/Weekly Loss、Drawdown、Leverage、Open Positions、Symbol/Strategy/Portfolio Exposure。账户级审批与 reservation 原子化，避免两个并发策略同时使用同一余额。撤单或确认失败才释放 reservation。
Spot 不允许借贷做空；价格/数量按 exchange tick/step 验证，quantity 舍入向下，低于 minimum 拒绝，不自动扩大订单。
Correlation Risk：先使用保守同向 crypto 风险篮子总敞口上限；Rolling Correlation/Beta/Factor 后续单独实现。数据不足不当作零相关。
Circuit Breaker 覆盖 Consecutive/Daily Loss、Drawdown、Stale Data、Disconnect、Order Errors、Abnormal Slippage、Position Mismatch。触发原因、开始时间、恢复条件可见；恢复需健康窗口与明确 reset，不靠单次正常消息自动解锁。
Kill Switch 先持久化账户/global gate，再停止新单；cancel open orders 单独审计。Close Positions 属额外确认操作，需新鲜仓位、reduce-only 能力与独立授权；Kill Switch 不承诺交易所已完成取消/平仓。UI 区分 requested/acknowledged/failed。
Daily/Weekly 账本边界用 UTC，rolling loss 与 calendar loss 明确区分；equity high-water mark 持久化，重启不清空亏损与 halt。
Paper 使用同一风控；Replay/Backtest 的 policy 版本写入 manifest。
