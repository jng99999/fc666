# 指标与市场结构合同 v1

输入为单一 instrument/timeframe 的连续、严格递增、已闭合 UTC Candle。价格/数量源字段保留 Decimal；计算边界显式转换有限 float，计算结果只用于研究，不用于记账。API 截止时间 as_of 必须带时区且不可未来；只提供 close_time <= as_of 的柱。

| 输出 | 默认参数与公式 | 首次可用 |
|---|---|---|
| SMA | close 算术均值，period=20 | 第 20 柱 |
| EMA | 首段 SMA seed，alpha=2/(period+1) | 第 20 柱 |
| Bollinger | SMA20 ±2×总体标准差，ddof=0 | 第 20 柱 |
| RSI | 14 个 close 变化的涨跌均值，Wilder alpha=1/14；全平为50，只有涨为100 | 第15柱 |
| ATR | TR=max(H-L,abs(H-prevClose),abs(L-prevClose))；首柱 H-L，Wilder14 | 第14柱 |
| MACD | SMA seeded EMA12-EMA26，signal EMA9，hist=MACD-signal | 第26柱；signal 第34柱 |
| VWAP | (H+L+C)/3 × base volume 加权，UTC open_time 日期重置 | 首个有非零量的柱 |
| confirmed_swings | 左右各2柱，严格高/低于邻居；相等不形成 pivot | 右侧确认柱闭合时 |

null 表示预热未满足或零成交量无 VWAP，禁止填0或前向填充。VWAP 是基于 OHLCV 的典型价近似，不是交易所逐笔 VWAP；日线每柱即独立 UTC session。
摆动点包含 pivot_time 和 confirmed_at；确认结果仅在确认行输出，不修改此前行。后续同类 pivot 给出 HH/LH/HL/LL 标签（相等为 EH/EL）；trend 为 close/EMA/SMA 的 up/down/mixed 简单关系，不冒充已完成 regime 分类器。

计算窗口决定 EMA/Wilder seed。API limit 1–1000，period 1–500，返回 seed_start 与 seed_policy。改变 limit 会改变递归指标起始条件，调用者必须记录该窗口，不声称与其他平台默认 seed 完全一致。批量 calculate 与逐根 IndicatorEngine 共用同一合同，回测可注入相同 as_of。
缺口、重复、乱序、混合 instrument/timeframe 直接拒绝；API 返回409。不会悄悄插值、跨缺口延续指标或修改源数据。晚到数据的 point-in-time 修订历史尚未实现；目前契约是 bar-close 可见性，不宣称具备实际 ingestion-time 历史快照。

只读端点 `/api/v1/indicators?symbol=BTCUSDT&timeframe=1m&limit=1000`。终端的“指标 overlays”开关显示 SMA20、EMA20、Bollinger 和 UTC VWAP；指标只到已闭合柱，30秒刷新，未闭合价格更新不会伪造指标。RSI/ATR/MACD 与确认点可在 API 获取，独立图表 pane、摆动点标注和完整 regime 后续实现。

## 第二切片：结构突破和指标pane
新增RSI/MACD/ATR独立pane，均线周期10/20/50/100和历史窗口120/500/1000。API支持oscillator_period(1–500)、swing_radius(1–20)，所有参数和seed仍随请求明确。
对最近已确认swing high/low用闭合价严格首次突破，分别up/down；每个level仅一次事件，新pivot重置level。首次/同向突破称BOS，已知方向反转称CHOCH；未确认pivot、影线touch、相等close不算突破。事件available_at为当前柱close，未来数据不能改变既往事件。这是明确的局部规则，不冒充所有市场结构学派或完整regime。
图上标记放在确认柱，完整事件在API返回；为可读性仅显示当前窗口最近20个结构标记。改变窗口会改变seed和初始结构状态，研究必须记录窗口。
