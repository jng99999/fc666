# 回放策略决策与前缀核对

新会话使用 closed-bar-replay-v2；可选择只回放行情、ema_long_flat_v1 或 sma_long_flat_v1。策略版本固定为1，参数在创建时验证并冻结。EMA周期必须与回放指标period一致；SMA fast_period必须小于slow_period。改变网页表单只影响下一次创建。旧v1会话保留原响应契约，不追加决策字段。

TARGET_DECISION按target-on-close-v1规则在每根已到达闭合柱产生LONG或FLAT目标，未成熟阶段不产生事件。available_at等于该柱close_time。每个成熟柱都有目标记录，连续相同目标也保留；这不是成交或订单，execution始终为NOT_IMPLEMENTED。没有账户资金、库存或回放成交。

决策由固定快照的已到达前缀重新计算；GET不会追加或投递事件。event_id由快照SHA与完整决策内容计算，不依赖会话UUID、游标revision或读取次数。相同快照逐根/多根推进及重置后重放得到相同ID。未来价格变化不影响已有目标和时序，但完整快照SHA及绑定它的事件ID会改变。因此事件ID不表示独立于数据上下文的信号身份。

页面展示最后100条决策；下载包含服务器确认的完整已到达前缀、全部对应指标和决策，保留原始响应的Decimal字符串和指标字段。下载不包含未到达数据或完整快照。播放期间及请求进行中禁用下载。

离线核对：

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.verify_replay_prefix prefix.json
```

工具接受最多4MiB的v2导出，核对游标、时间、连续闭合柱、品种/周期、指标与策略决策重算结果及事件ID。它验证已到达前缀的内部一致性；未到达数据没有导出，不能验证完整快照SHA所绑定的全部历史，也不提供签名或来源真实性证明。旧v1导出暂不支持此核对工具。
