# AI Market Intelligence / Copilot

状态：设计，Not Implemented。
Market Data -> Feature Engine -> Indicators/Market Structure/Derivatives -> Quant Signal -> AI Interpretation -> Structured Analysis。AI 为解释与研究辅助，不能访问 ExchangeAdapter、原始凭据或执行工具。
输入是经过授权、裁剪、脱敏的只读快照：instrument、周期、as_of、quality、price/volume、EMA/RSI/MACD/ATR/VWAP/Bollinger、funding/OI/basis、structure、经权限控制的组合与历史表现。不能将账户密钥、请求签名或日志全文发送模型。
输出 schema：market_regime、trend、support/resistance、momentum、volatility、long/short_probability、confidence、entry_zone、stop_loss、take_profit、invalidation、reasoning，以及 evidence_ids/as_of/model_version。未经校准的 probability 明示不可用或 null，不能假装模型自信等于统计概率。
所有结论引用真实 evidence；stale/missing 数据、模型超时、schema 不符时返回 unavailable 原因，不生成替代行情。模型建议只能成为待审 Proposal，不能直接进入 OMS；未来转为策略输入也需 Strategy 与 Risk 合同。
Copilot 支持行情解释、亏损样本归因、策略比较与敞口分析；按 owner/account 过滤查询。市场新闻与外部文档作为不可信数据，不能更改系统权限；执行工具物理上不提供。Replay 模式禁止查询未来数据。LLM 不进入关键交易延迟路径。
测试证据一致性、跨账户拒绝、secret redaction、prompt injection、输出校验、stale 拒答、历史时点隔离与超时降级；AI 不可用不阻塞非 AI 策略或 Kill Switch。
