# 历史 Spot Paper 账户契约

Phase8首片提供真实数据库闭合OHLCV历史驱动的独立模拟账户，入口/paper，版本spot-paper-next-open-v1。当前不包含实时Paper、交易所私有接口、资金划转、杠杆/卖空、手动/限价/止损单、逐笔撮合、组合风险或多用户权限隔离。实盘关闭。

## 时间与资金

POST /api/v1/paper/sessions捕获2..1000根同品种/周期连续闭合柱、当前交易规则、EMA/SMA v1参数、BacktestConfig和RiskLimits。只支持BTCUSDT/ETHUSDT Spot六周期。初始cursor=0，现金为initial_cash、库存及费用为零；初始时钟为首柱open_time。每个账户有独立资金，不与其他账户或回测共享持仓。

收盘产生LONG/FLAT目标，在下一根已到达柱open_time尝试执行；没有同柱收盘成交。LONG仅在库存为零时买入，FLAT在有库存时卖出；不加仓、不持续再平衡。初次预热没有目标。末尾目标标记NO_NEXT_BAR，不成交、不强制平仓。返回的candles、signals、equity、orders/fills仅属于当前已到达前缀，未到达价格不进入任何消费计算。快照时间范围、柱数和SHA可事先知道。

价格按不利滑点和tick取整；数量按quantity_step向下取整，校验最低数量/金额。买入使用可用现金乘allocation并预留quote货币手续费。成交容量=前一已闭合柱base volume乘participation，是流动性代理而非真实开盘成交量。IOC部分成交余量取消；持仓未全部卖出时可在后续FLAT目标继续尝试卖出。未成交订单记录明确拒绝原因。

现金、库存、加权成本、费用和已实现/未实现盈亏用Decimal精度60计算，输入金额拒绝float/bool；API与导出保留Decimal字符串，网页金额只显示两位小数。每根验证现金/库存/成本非负及equity-initial_cash=realized_pnl+unrealized_pnl（计算误差容差1e-40）。权益按已到达收盘计价，费用计入买入成本、卖出净收入。

## 最低风险限制

max_order_quote限制一次实际买入成交金额，max_position_quote限制该次买入后的库存按成交价格计价金额。先计算容量/规则后的可成交数量，再拒绝超限买入，不自动裁切到风控限额。两项均只限制增加风险的买入，卖出不受金额上限阻碍。持仓涨价可能超过买入时限额，这不是持续敞口限制。

max_drawdown为(历史观测peak-equity)/peak。按每根已到达开盘（成交前）及收盘观察，达到或超过阈值即锁定禁止后续买入，即使之后恢复也不解除。没有柱内止损或自动平仓。手动halt同样阻止后续买入，仍允许策略卖出；halt_at保存操作时的cursor，只影响下一根及之后，不回写此前成交。两者不停止历史推进，不能当作完整OMS Kill Switch。

没有解除停止命令。显式reset清空本次现金变化、持仓、费用、订单/成交和停止状态，保留原快照/参数后从零根模拟；网页明确提示该作用。

## 持久化、幂等与恢复

schema0006新增paper_sessions：固定snapshot、cursor/revision、halt_at及完整已确认ledger JSON。快照SHA包含历史、规则、策略、配置和风险参数。GET在已到达前缀重算并核对持久账本；快照或账本不一致则拒绝操作，不静默修正。改变共享公式前必须冻结本版本实现或显式增加版本，不能破坏已有账户重构。

POST /.../{UUID}/command支持step(count1..10)、halt(count1)、reset(count1)，必须给expected_revision。FOR UPDATE、版本验证、游标/停止状态、账本与revision更新在同一PG事务；相同旧revision最多一项成功，其他409。实际状态不变（结束后step、重复halt、已在零根且未停止的reset）不增revision。账本重算不向外部系统发送订单。

订单ID由快照、引擎和该次目标时序/方向确定；相同快照重置后重放保持ID。ID标识模拟订单尝试，同一尝试在手动停止后可从成交变成拒绝。全历史不同会改变快照及绑定ID，但相同已到达价格不会因未来价格变化而改变资金/目标。ID与SHA没有签名或真实性保证。

网页手动逐根推进，没有后台自动时钟。刷新恢复最后账户的确认进度与固定表单参数。操作失去响应或发生409后只GET同步，不自动重发POST；未恢复时保留最后确认视图。创建请求丢失响应时不会自动重试，可能留下未知UUID的空账户；会话列表与保留/清理仍待实现。

## 下载与离线核对

下载完整原始已确认响应，包含已到达历史、规则、参数、风险状态和资金/订单/成交；不包含完整snapshot或未到达数据。

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.verify_paper_prefix prefix.json
```

最多4MiB；验证时钟、游标、连续闭合历史、Spot规则与整个资金/风险/成交重构一致。它验证前缀内部一致性，不验证未导出历史的完整快照SHA、来源真实性或手动停止操作真实性；不替代签名审计或实盘对账。

实时闭合柱Paper首片已追加，使用独立版本与页面，见REALTIME_PAPER.md；本历史模型不变。完整Phase8及Phase9/OMS仍待完成。
