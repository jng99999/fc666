# Strategy SDK

状态：设计，Not Implemented。

统一 Strategy.on_event(context, market_event) -> list[Signal]，on_start/on_stop/on_fill 为生命周期入口。Context 提供 as_of、只读指标窗口、portfolio/position 快照、market_context、配置与注入时钟；禁止直接访问 Exchange、数据库、网络、密钥和系统实时时钟。Python 导入约束不能充当恶意代码沙箱，用户自定义策略需独立受限 worker。
BACKTEST/REPLAY/PAPER/LIVE 共享策略核心，仅时钟、数据源和撮合/执行端口变化。策略状态快照带版本，事件可确定性重放；同一输入与 seed 应得到同一 Signal。
Signal 合同见 [DATA_MODEL](DATA_MODEL.md)。position_size 是建议数量，不能绕过 Risk。confidence 的定义与可校准性随策略版本记录，不宣称胜率。
只交付 as_of 之前 available_at 已到达的数据；多周期仅提供已闭合高周期 K 线。warmup 未满足返回无信号，并记录原因。指标、regime、swing 确认都存确认时刻；不能把未来确认结果回填到过去可见状态。
测试：未来后缀修改不改变历史信号、不同模式相同序列结果一致、重复事件不重复发信号、状态快照恢复一致、非法数量/未知 instrument/NaN 被拒绝。
