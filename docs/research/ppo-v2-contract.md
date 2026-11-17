# PPO v2 研究推理契约（2026-10-22）

实现位于独立克隆 `work/fc666/core/gold/rl_experiments/__init__.py`。供父线程审查；不授权模型准入、部署或下单。模型仅为本地自生成文件，禁用任意上传和 pickle 模型接收。

## JSON 封装

`version = gold-ppo-input-ablation-v2`，`input = raw_v1_observation`，`arm = A | B | C`。
`actions = ["0", ".25", ".5"]`，索引分别为 0%、25%、50% **目标仓位**，并非一次性买入指令。
`train_end = 1704067200000`（2024-01-01 00:00 UTC，训练仅截至此前），`timesteps = 131072`，`seed = 7 | 19 | 42`；`dataset` 固定为 `ee9afe981db266959ebcc1557e6120ae6e60012f761265ed46d57ab57878276c`。
`weights_sha256` 为 `layers` 的 UTF-8 canonical JSON（sort_keys=True, separators=(',', ':'), ensure_ascii=True）SHA-256。整个 actor 文件摘要见实验 manifest。

## 原始输入（长度 10，float32，有限数）

严格顺序：`return_1, return_4, return_20, range_20, volatility_20, spread_fraction, elapsed_hours_log, cash_fraction, exposure, drawdown`。

前七项复用冻结 v1 `market_features`：对数收盘收益 1/4/20 观测柱；20 柱 high-low/close；20 个 1 柱 log-return 标准差(ddof=0)；close paired spread/close；log1p(相邻实际柱起点间隔/HOUR)。输入只能使用已结束的完整真实 1h 柱，暖机 100 根；缺口保留，不补平柱。v1 原始市场特征先裁剪 [-10,10]。注意 20 柱并不等于 20 个自然小时。

账户三项为当前 bid 估值下 cash/equity、quantity*mark/equity、(peak-equity)/peak；mark=mid_close-spread_close*spread_multiplier/2。账户和估值必须来自同一执行器，不可用未来成交价构造状态。v1 原始完整观测裁剪 [-10,10]。

## 缩放与模型

A: `scaler = null`，保持原始输入。
B/C: `scaler` 含前七特征的冻结 mean/std、顺序、ddof=0、clip=[-10,10]、拟合样本数/首末可用时刻/train_end。只拟合 2023 `market_features(train_bars)[99:-1]` 的 **5038** 个决策观测；最后 truncation 观测不用于拟合。
B: 对原始前七项执行 `(raw-mean)/std`（float64 中间计算），裁剪 [-10,10] 后转 float32。
C: 同 B 变换并校验后将前七项置 0；账户三项保持原定义。C 仍可能通过内生账户状态间接包含历史价格信息，不能描述为完全无信息。
所有臂最后拼接账户状态、完整裁剪 [-10,10] 并转 float32。

`layers`: Linear(10→32), Tanh, Linear(32→32), Tanh, Linear(32→3)。Linear 为 `weight @ value + bias`，权重按输出行排列，全部 float32；确定性动作是三个 logit 的第一个 argmax。不得将外部已缩放输入再次送入此原始输入接口。v2 必须注册独立版本，不能冒充 v1。

拒绝非有限输入/权重、错序/错维度、未知版本/动作/层、B/C 缺少冻结尺度、A 带尺度、非正或 NaN 方差。不为常量列自动设置 std=1，不在线更新尺度。

本实验逐步比较保存重载的 PPO 与磁盘 JSON actor 的动作，覆盖每个臂、种子、2024 双成本场景。训练截止/权重摘要仅作溯源元数据，不等于受信签名；服务端准入仍需独立可信摘要白名单及严格 schema 校验。

## 执行与限制

原环境在观测 close 冻结动作，下一真实小时边界 paired bid/ask 执行；quote 缺失或晚于边界 120 秒取消，不延后补成交。费用 0.10/oz、额外滑点 0.05/oz；双倍压力同时加倍 spread/fee/slippage。数量步长 0.01oz、单笔上限 10oz、Decimal 60 位计算及守恒。历史快照报价不是当前真实可成交报价；缺少深度、延迟、融资与隔夜费模型。

2024 是已知验证窗；2025 已看过且本轮不评价。三种子/一年训练不足以证明统计显著或未来盈利。没有前向模拟证据前保持研究状态。

## 数值契约协调补充（执行线程同步后）

上述 `gold-ppo-input-ablation-v2` 是 **native f32 研究导出**，不能直接替换线上 `gold-policy-json-f64-v1`。九次预注册训练及原始 f32 验证保持不变。

另运行不重训的部署候选转换/兼容性审计：读取自生成 actor 的有限权重和冻结 scaler，以明确的新版本处理纯 Python f64 原始十维观测。市场波动 std 使用 `sqrt(fsum((r-mean)^2)/20)`，账户值从候选自己执行轨迹的 Decimal 状态重新计算。前七原始值先裁剪 [-10,10]，B/C 再按冻结 mean/std 以 f64 标量逐列缩放、再次裁剪；C 最后将七维置零；账户三维不缩放。

推理采用执行线程受控源码中实际 `infer` 算法（保存其源码/AST 摘要）：每行 `math.fsum(w*v for w,v in zip(weights,values)) + bias`，然后 `math.tanh`，最终 first argmax。不得声称与 native float32 数值或跨平台逐字节一致。每步记录与同轨迹 native PPO 的动作差异、最小 top1-top2 logit margin；完整候选收益/成本/回撤另行报告。阈值在审计前冻结为 `1e-6`：任何 margin <= 阈值都会阻止候选准入，部署对照记录拒绝并消耗该小时，保留已有仓位；仅将未拒绝时的唯一 argmax 计为动作。

执行线程已补齐 `gold-policy-ablation-f64-v2`（观测仍为 `gold-hourly-observation-f64-v1`）。研究侧转换器使用该明确版本，`input=raw_f64_observation`、`arithmetic=python_f64_fsum_tanh_margin_1e-6`，另记录 `native_actor_sha256`，保留原权重摘要、原训练元数据和 scaler。它通过实际 `validate_actor/validate_preprocessor`，不是只改 tag。

数值评估直接从受控执行线程源码加载 `observation_values/_observation_values/preprocess/infer` 六个纯函数与其实际校验函数；完整源码摘要、helper AST 摘要及副本随报告保存。`observation_values` 固定 Decimal precision28、bid 估值、独立逐步更新的 bid peak；原始 f32 观测则继续使用冻结 v1 环境自己的定义。尤其双倍点差场景二者账户估值定义不同，所有差异必须记录，不声称只改变浮点位数。推理在 actual runtime 中 margin <=1e-6 时拒绝；评估消耗该小时但不发目标0指令、不改变已有数量，将 abstention 单独计数。

历史 HistData 与线上 Swissquote 来源差异及真实前向资格仍须单独验证；格式/数值适配通过不自动等于策略获准。模型候选保持未注册，最终是否满足研究门槛由完整九模型两成本结果决定。
