# Paper 执行前逐笔规则授权契约

本切片将闭合柱批次中预计产生的每笔模拟订单，在准备事务中独立持久化规则授权。授权是本地冻结规则的可核对决定，不是操作者身份认证、人工逐笔审批、交易所请求或 ACK。Live 保持关闭。原 `spot-paper-closed-close-v1` 模拟公式、稳定订单 ID、风险拒绝原因、准备及独立终态契约继续有效。

## 执行前事实与身份

账户行锁下，准备路径以原冻结快照、原 observations、halt 及本批 observations 创建临时候选对象，运行原模拟引擎。原账户账本、余额、库存、observations 和 revision 不接受这些候选经济效果。候选新订单集合是候选账本相对于已有账本新增的完整订单；没有新增订单的批次可以有零条逐笔授权，不能把策略 pending target 补成订单。

授权载荷保存契约及策略版本、账户 UUID、准备 ID、冻结快照摘要、完整预计订单与对应预计模拟成交或 `null`，以及账户执行门禁和逐笔决定。预计成交中的数量、价格、费用和成交后余额只是原引擎的候选计算结果，未发生独立终态提交；页面必须明确显示“预计”，不能宣传为已成交或已确认。账户、准备和原订单共同限定身份，避免不同账户或批次复用授权。载荷摘要与确定性身份须重算并完整比较，不能只核对 ID 或摘要字符串。

授权行与新准备记录在同一个第一阶段事务中提交。在该事务提交之前无独立授权事实；提交后响应丢失，重试须找回同一准备及同一完整授权集合。已有同身份不同内容必须失败，不得忽略唯一冲突。授权不可更新、删除或从拒绝改为允许；准备取消仍保留原授权，不能将旧决定改写为后来的账户状态。

实际版本为 `paper-simulated-authorization-v1`。每行保存 authorization_id、preparation_id、order_id、带时区 recorded_at、payload 与 payload_sha256；数据库唯一约束覆盖 preparation_id 与 order_id。完整 payload 字段为 version、session_id、preparation_id、snapshot_sha256、engine_version、account_gate、decision、reason、proposed_order、projected_fill、projection_only=true、external_submission_allowed=false。authorization_id 由版本、准备 ID 和原订单 ID 摘要生成；准备 ID 已包含账户 UUID 及完整冻结准备载荷，构成账户命名空间。没有单独的人类权限策略 ID，当前授权策略版本由契约 version 标识。

## 账户门禁与逐笔决定

账户门禁冻结准备时的生命周期和原风险信息。RUNNING 账户仅在原门禁与原风险允许时进入模拟；PAUSED 或终止生命周期不得生成获准成交。手动 halt 或回撤停止限制新买入，原引擎允许的卖出仍可减少持仓。门禁不能把整个 halted 账户描述为任何方向均禁止，也不能因逐笔授权重新开放账户。

account_gate 精确包含 account_status、buy_allowed、sell_allowed、entry_halted、scope=LOCAL_PAPER_RULES、external_submission_allowed=false。buy_allowed / sell_allowed 是生命周期与方向的初始必要条件，不承诺本批具体订单一定获准；行情门控、批次内风险变化、金额限制和市场容量仍决定逐笔结果。

| 候选原订单结果 | 逐笔规则决定 | 预计成交 |
| --- | --- | --- |
| FILLED / PARTIAL_CANCELLED | ALLOW_SIMULATION | 保存原候选成交，不额外放大数量 |
| REJECTED | DENY_SIMULATION | `null`，保留原拒绝原因 |

原引擎的 PAUSED、PRE_ACTIVATION、STALE_BAR、MANUAL_HALT、MAX_DRAWDOWN、市场规则、容量和金额上限等拒绝事实必须原样保存。批次内风险变化仍由原引擎逐笔计算；批次开始的汇总门禁不能覆盖后来达到回撤上限的拒绝。ALLOW_SIMULATION 仅对本批原候选订单有效，不能用于后来行情、其他账户、私有 API 或人工修改数量后的订单。

## 消费前核对与原子效果

第二阶段沿用账户先于准备的锁顺序；任何经济效果提交之前，冻结 base、控制、当前 Instrument、已接受行情前缀、本批完整行情、消费时限和全部授权都必须通过。使用相同冻结输入重算候选订单和授权计划，对已保存的全部授权逐项核对：版本、账户和准备关联、身份、摘要、门禁、完整原订单、预计成交、决定与拒绝原因。遗漏、多余、重复、错账户或损坏均不能接受。

只有全部核对成功，才在同一个消费事务中接受本批 observations、修改账本及余额、持久化原模拟订单终态并将准备标记 CONSUMED。授权不自行修改资金；读取授权不消费批次。消费事务任意步骤失败，整组经济事实和准备终态回滚。提交后丢响应或重建连接后的重复消费返回已有终态，不产生重复经济效果。

准备时有授权不代表消费时依然可执行。控制变化、行情修订和过期继续走既有明确取消规则；旧授权保留作检查证据，不自动重放、修补或重新授权。完整性损坏须失败并回滚，不能伪装为普通来源修订。

## 历史准备兼容

准备记录新增可空 `authorization_version`。新准备使用当前逐笔授权版本；部署前 `NULL` 明确表示没有执行前独立授权证据。旧 PREPARED 在消费路径取消并记 `AUTHORIZATION_MISSING`，不能临时补授权后继续消费；必须重新准备才可能获得新执行资格。已经 CONSUMED 或 CANCELLED 的历史准备保持原终态，读取显示授权不可用，不追认过去存在执行前许可。

零条授权只有在带当前版本且重算确实没有新增订单时才可构成完整结果。未知版本、带版本却缺少应该存在的授权、或旧版本下出现不一致记录不得当成历史兼容自动修复。既有准备载荷和旧模拟账本不因新增标记而改写。

## 读取与验收边界

只读 GET `/api/v1/paper/streams/{UUID}/authorizations` 及实时 Paper 页展示账户门禁、批次关联、ALLOW_SIMULATION / DENY_SIMULATION、拒绝原因、预计订单与预计成交，并明确区分准备、消费和取消状态。接口与页面不写账户、准备或授权，不恢复交易，不提供人工改授权或重新执行按钮。读取失败时清除旧确认。

读取在 REPEATABLE READ 中核对当前账户账本；每个带版本准备，按该批首根行情之前的历史 observations 重建原 base，再精确比对冻结 base 摘要及完整授权计划。历史重建使用该准备保存的 revision、生命周期及 halt，不拿账户当前控制替换过去的授权事实。响应保留当前 account_gate，并在 batches 中分别给出批次状态和 VERIFIED / LEGACY_UNAVAILABLE。每账户最多保留1000个准备，但本接口只完整核对最新20批次，明确返回 total_batches、window_limit=20 和 has_older；更早批次保留，不在本次核验范围，历史分页尚未实现。每个选中批次的授权必须完整核对，不能截断逐单计划；响应超过32 MiB拒绝。这样限制历史重算量，不把部分窗口宣传为全历史核验。

本阶段授权检查不提供独立离线复算工具或完整冻结输入下载。授权 recorded_at 不得早于准备 created_at、晚于当前时刻或已结束批次的 finished_at。对 CONSUMED 批次还必须匹配已接受 observations 和原 orders/fills，单独写入 CONSUMED 标记不能冒充真实消费。读取报告中的摘要不证明外部来源真实性、操作者身份或数据库历史，也不能单独证明授权先于成交提交。必须通过数据库事务和故障验收验证两个提交边界。既有原账本、恢复及已完成订单意图的离线核验工具继续有效。

验收覆盖允许买入、halt 后拒绝买入和允许卖出、原风险拒绝、暂停与无新订单批次、授权不可变约束、准备提交前回滚、准备提交后丢确认、消费回滚、重复及并发消费、损坏授权拒绝、控制或行情变化、过期、历史准备取消与历史终态不可追认。连接重建不能宣传为完整进程或基础设施重启验收。

本切片没有共享资金风控、人类身份授权、真实提交幂等键、UNKNOWN_SUBMISSION、交易所回执或外部 exactly-once。完整实盘 OMS、账户权限及独立交易所对账仍须后续设计与验证。
