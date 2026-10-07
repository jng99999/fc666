# Execution / OMS / Reconciliation

状态：实盘 OMS 仍为设计，Not Implemented；LIVE_TRADING=false。Paper 已实现独立持久化的完成态模拟意图、同事务状态路径与只读核对，见 PAPER_ORDER_INTENTS.md；不是执行前持久队列或交易所提交状态机。

Signal -> RiskDecision -> OrderIntent -> OMS -> ExchangeAdapter -> OrderUpdate -> Fill -> Position -> Portfolio。
状态：CREATED -> VALIDATED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED；未成交订单可进入 CANCEL_PENDING -> CANCELLED，或 REJECTED/EXPIRED。PARTIALLY_FILLED 可继续成交或取消余量。cancel acknowledgement 与 fill 乱序需按累计成交量对账，CANCEL_PENDING 期间仍可能成交。禁止用几个 boolean 代替状态机。
提交超时的 UNKNOWN_SUBMISSION 是查询/恢复状态，不是宣称交易所拒绝。稳定 client_order_id 在首次提交前持久化；重试先查订单/近期成交与仓位，不盲目重复 create_order。找不到且交易所查询不可靠时 halt，保留人工解决记录。
累计成交数量单调且不超过请求量；terminal 状态晚到的已存在成交不得丢失。Fill ledger 唯一约束，仓位由成交与费用重新推导。只按 ack 改账不可靠。

## Adapter 能力
公共 get_markets/ticker/orderbook/ohlcv/trades/funding/open_interest；私有 get_balance/positions、create/cancel/modify_order、get_order/open_orders/order_history/trade_history。接口返回类型化 capability，不支持 modify 等操作时明确报错。Rate limit、重试与 exchange error 映射独立，write retry 不复用 read retry 策略。

## 启动与断线恢复
Connect -> Fetch Balance/Positions/OpenOrders/RecentTrades -> Load Local State -> Compare -> Reconcile -> Verify -> Enable Trading。
全过程账户执行 gate 关闭；断线不能假设未成交。分页拉取近期历史，重叠窗口加 fill dedupe；订单数量/费用/仓位对齐，audit 保存差异与证据。交易所快照与增量交错时重复拉取到一致 watermark，未得到稳定结果不放行。
数据库不可用、所有者 lease 过期、差异无法解释、凭据失效或市场数据 stale 均停新订单。人工处理有 actor/reason/audit，不允许静默“以本地覆盖交易所”。
实盘验收需覆盖超时已成交、重复回报、partial fill/cancel race、断线成交、双 worker、restart、position mismatch、kill switch；Phase 13 独立发布与授权。
