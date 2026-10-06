# 实时闭合柱 Spot Paper 契约

/paper/realtime提供spot-paper-closed-close-v1模拟账户。后台读取公共行情worker已经持久化的BTCUSDT/ETHUSDT六周期闭合柱；没有交易所私有请求、资金划转、实时逐笔/订单簿撮合、杠杆、手动订单或实盘。历史/paper及spot-paper-next-open-v1保持原契约，不能混用两个成交模型。

## 激活与成交时序

POST /api/v1/paper/streams在创建时冻结2..500根连续已闭合历史、交易规则、EMA/SMA参数、资金/成本及风险参数。as_of由服务器创建时确定，客户端不允许指定过去截止。历史只预热指标和策略，账户现金等于initial_cash、库存/费用为零，没有预热历史成交。

新柱按连续open_time接受。上一根收盘的目标，只在下一根闭合柱及时被消费时，以该柱close乘不利滑点并按tick取整为模拟参考价。execution_at是参考柱close_time，observed_at是worker本次扫描时间；decision_at必须严格早于execution_at。这不是事后按下一柱开盘造出的成交，也不保证参考价在观察时真实可成交。前柱base volume乘participation作为IOC容量代理，规则、费用、long/flat转换和风险金额限制沿用已定义的资金语义，但使用独立版本实现。

max_lag_seconds严格整数1..60，默认15；扫描时间距柱close_time超过阈值则标记STALE_BAR，更新指标/收盘权益/下一目标，但该柱所有模拟订单拒绝，不补造过去成交。恢复后新的及时柱可按最新已知目标执行。即使创建前闭合的柱晚写入公共库、仍在延迟阈值内，也标记PRE_ACTIVATION而不成交。

一旦接受，candle、observed_at与gate不可被worker重写。gate取ELIGIBLE/STALE_BAR/PAUSED/PRE_ACTIVATION。初始快照SHA只绑定冻结的预热历史/规则/请求；新增观察不会改变这个SHA，订单ID依初始SHA、版本、目标时间、参考柱时间和方向生成，重复扫描与重启不更换已有ID。

## 风险与生命周期

仍限制实际买入金额、买入后持仓按成交参考价计价金额、Decimal非负资金/库存/成本及PnL守恒。回撤按已接受收盘参考价，在成交前/后观察；超过或达到阈值永久阻止新买入，允许策略卖出。没有柱内止损或强制平仓。手动halt从当前accepted_bars之后阻止买入，允许卖出。

RUNNING接受连续闭合柱并按gate模拟；PAUSED继续接受柱和更新指标，但拒绝全部成交。resume只从PAUSED恢复，没有补成交已暂停的柱。STOPPED停止接受数据和所有执行、保留资金/库存且不能恢复；不会自动平仓。BLOCKED同样停止执行，必须核对原因后创建新账户。达到预热+新增1000根时LIMIT_REACHED，停止接受与执行；没有自动延长或丢弃旧账本。每个账户独立资金，非共享组合。

pause/resume/halt/stop使用expected_revision及count1，通过FOR UPDATE、控制状态/账本/版本同事务修改。worker接受新柱也增加revision；409必须读最新状态，不自动重发。重复相同已生效操作不增revision。没有reset或手动step端点。

## 缺口、修正与恢复

每次接受前核对所有预热与已接受柱、当前交易规则。共享行锁保证核对/接受不会与正式修正写入交错；修正在消费事务之后发生时，下一扫描检测并阻断，不倒改已发生账本。DATA_REVISED、DATA_MISSING、RULES_CHANGED进入BLOCKED，保留原冻结输入、成交及资金，不自动迁移或恢复。

未接受历史的缺口为GAP，绝不跳过；等公共行情补齐。缺少下一柱且超出其预期闭合+延迟阈值时为STALE。公共worker恢复仍按原确认修正/补数规则运行，Paper自身不调用外部API或删除行情。

schema0007新增paper_streams（冻结snapshot、append-only observations、完整ledger、控制状态、halt_at、revision及feed）和paper_workers心跳。独立apps.worker.paper每0.5秒轮询，最多20个RUNNING/PAUSED账户，全局事务锁原子限制创建容量；每次最多追加10根，全历史最多1000根。FOR UPDATE SKIP LOCKED保证并行worker不重复提交同一账户成交。现金、库存、输入与进度在一个PG事务中提交；GET从所有已接受输入重构并核对持久账本。快照或账本不一致拒绝读取/执行，不静默修复。worker异常按账户隔离，不把进程心跳当作每个账户成功运行证明。

/api/v1/paper/status根据10秒内worker心跳报告健康，独立于API工程readiness和公共行情健康。worker退出清除自己的心跳；崩溃后心跳会过期。恢复进程即可继续固定账本，迟到柱不会补造成交。五进程为API/web/research/market/paper；start.sh执行最新迁移并恢复它们，不新增外部密钥/域名/包依赖。

## 网页与离线核对

网页2秒轮询确认状态。关闭网页不停止账户；需要pause/stop明确操作。刷新恢复UUID、参数和已确认账本。操作丢失响应只GET同步，不自动POST重试。初次创建丢失响应可能遗留未知UUID账户，账户列表/保留/清理仍待实现。

下载包含完整已接受预热和观察、初始规则/参数、控制/风险状态及账本，保留原始Decimal精度。金额显示两位小数只是展示。

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.verify_paper_stream stream.json
```

最多4MiB，核对初始快照SHA、连续时序、观察延迟和整个资金/订单/成交重构。输入均已到达，没有未到达数据。工具验证内部一致性，不证明公共来源、观察时间或控制操作的真实性，没有签名审计/实盘对账保证。

这是有界实时闭合柱Paper首片，尚未完成完整OMS、组合风险、多用户鉴权、账户列表/清理、长时性能和灾难恢复验收。修改共享公式前须保留已发布版本重构；不能破坏已有账户/历史导出。
