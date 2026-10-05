# Backtest 与 Replay

状态：设计，Not Implemented。

Vectorized 用于研究筛选，Event Driven 用于行为验证；前者不能凭高收益代替后者验收。
事件链为 Market Event -> Strategy -> Signal -> Risk -> Order -> Matching -> Fill -> Portfolio -> PnL；策略、订单与风控合同与 Paper/Live 共享。

## 撮合规则
只使用决策时已知数据。基于 bar close 的信号最快在下一可用事件成交，禁止同一收盘价无延迟成交。单根 OHLC 内无法判断 SL/TP 的先后时，采用保守规则并标注 ambiguity，或要求更细数据。Limit touch 不等于必然成交；成交受可用真实 volume/盘口与 participation cap 约束。订单创建之前的 high/low 不可用于成交。
支持 Long/Short、Spot/Futures、Market/Limit/Stop/StopLimit，区分 capability；未支持类型明确拒绝。模拟 maker/taker fee、spread、slippage、funding、partial/rejected/cancelled fill、tick/step/min-notional。Cross/Isolated、leverage/liquidation 按交易所保证金规则建模后单独验收，不能假装 Spot 引擎支持。
latency simulation 为后续增强，当前模型假设写入 run manifest。

## 分析输出
Total/Annualized Return、Sharpe、Sortino、Calmar、Max Drawdown、Win Rate、Profit Factor、Expectancy、Average Win/Loss、Risk Reward、Trade Count、Exposure、Long/Short Performance、Fees、Funding、Slippage。
每个指标记录频率、年化 convention、risk-free rate；无交易/零分母输出 null 与 reason，不填 0 掩饰。Crypto 连续交易市场年化 convention 明示。提供 Equity/Drawdown、Monthly Returns、PnL/Trade Distribution、Holding Time、Win/Loss Streak、PnL by Time/Regime；图表关联 entry/exit/fill 与当时指标。

## Replay
虚拟时钟驱动 Chart/Indicator/Strategy/AI/Order/Portfolio；Play/Pause/Step、1/5/10/50X。暂停时不推进策略时间；seek 由最近状态快照重放，不挪动游标后沿用旧仓位。AI 在历史模式只允许读取 as_of 数据。后台 job 支持 cancellation、进度、失败原因、资源上限；取消任务不可留下半成品结果标为 success。
验证 future leakage、手续费资金守恒、partial fill、SL/TP ambiguity、重复事件、cancel race、跨模式结果一致、重启恢复、真实历史数据 manifest 复现。
