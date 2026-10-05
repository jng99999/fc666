# 数据与时间合同

当前实现说明：Phase 1 已实现 Instrument/Candle typed/ORM 模型、Decimal(38,18)、UTC、唯一键与 OHLC 约束、TimescaleDB hypertable；其他对象均未实现。

状态：设计，Not Implemented。

## 核心对象
| 对象 | 必要字段与约束 |
|---|---|
| Instrument | instrument_id、exchange、native_symbol、display_symbol、base、quote、market_type、contract_type、tick_size、quantity_step、min_quantity、min_notional、contract_size、settlement_currency；版本化交易规则 |
| Candle | instrument_id、timeframe、open_time、close_time、open/high/low/close/volume、is_closed、source、quality、revision |
| Trade | exchange_trade_id、instrument_id、event_time、received_at、price、quantity、aggressor_side |
| OrderBook | instrument_id、generation、last_sequence、snapshot_time、bids/asks、validity |
| Derivatives | Funding/OI/Mark/Index/Basis，单位、来源、observed_at、effective_at；缺失为 null，不用 0 |
| Signal | signal_id、strategy/version、instrument_id、side、signal_type、entry、stop_loss、take_profit、confidence、position_size、reason、metadata、timestamp |
| OrderIntent | intent_id、client_order_id、account、mode、instrument、side/type/quantity/price、risk_decision_id、created_at |
| Order/Fill | exchange_order_id、status、cumulative_quantity、fill_id、price/quantity/fee/currency、event_time |
| Position | account/mode/instrument、side、quantity、average_entry、mark_price、leverage、margin、liquidation_price、realized/unrealized_pnl |
| BacktestRun | strategy_hash、dataset_hash、config_hash、seed、engine_version、status、metrics、artifacts |
| AuditEvent | event_id、actor、account/mode、correlation_id、action、reason、event_time |

instrument_id 区分交易所、Spot/Perpetual、结算币和合约；BTCUSDT 与 BTC-USDT-SWAP 不能仅按 BTC/USDT 合并。native_symbol 仅存在于 adapter 映射。

## 数值与时间
价格/数量/费用/账本用 Decimal，数据库 NUMERIC（精度与尺度在 Phase 1 按交易所规则验证）；JSON 用十进制字符串。指标研究允许 float64，边界转换显式检查。禁止浮点金额直接落账。
内部 UTC aware datetime，wire timestamp RFC3339 Z；原生毫秒/微秒由适配器按声明转换。另存 received_at 与策略 available_at；不得把“事件发生时间”当成“系统已知时间”。延迟测量用单机 monotonic clock，不直接减两个未校时机器的时钟。
Candle 唯一键 instrument/timeframe/open_time，OHLC 必须满足 low ≤ min(open,close) ≤ max(open,close) ≤ high，volume ≥ 0；closed bar 修订有版本及审计。数据缺口显式记录，不填出虚构成交；策略遇缺口按配置暂停。历史导入事务 upsert 且可重跑。
Fill 唯一键 exchange/account/fill_id，意图唯一键 account/mode/client_order_id。数据库约束与应用检查同时保证幂等。

## 存储规划
PostgreSQL：users/accounts、strategy_versions、order_intents、orders、fills、positions、risk_decisions、audit、jobs、outbox、workspace_preferences。
TimescaleDB：candles、public_trades、funding、open_interest、market_metrics；分区键与保留周期先按两币种实测，不默认无限保留 tick。
Redis：最新行情、订阅分发与缓存。历史研究数据与任务 artifacts 保留内容 hash、来源、时间范围及质量报告；策略版本与数据 manifest 可复现。
