# Phase 4 首批指标与确认结构报告

日期：2026-10-05。状态 PARTIAL：基础指标计算与价格 overlays 已交付，完整 market structure/regime 和全部展示未完成。

实现 SMA、EMA、RSI、ATR、MACD/signal/histogram、Bollinger、UTC-session 典型价 VWAP，支持因果逐根和批量。确认 swing high/low 与 HH/LH/HL/LL/EH/EL；每个 pivot 区分发生/确认时刻，不回填过去输出。规则、seed/warmup/as_of、缺口和浮点边界见 INDICATORS.md。
API 实际读取 TimescaleDB finalized bars，参数校验/未来时点拒绝/缺口409/空数据；前端同源白名单代理和 Zod 验证，ChartAdapter 管理五条价格指标线。指标错误时清理指标线；开关关闭后隐藏。指标只到闭合柱，每30秒刷新；未闭合柱仍仅更新价格。

## 验证
基础验证已通过65项后端测试、迁移一致性、TypeScript和生产构建；随后增加相等 swing 标签回归；最终66项后端测试通过，类型检查与生产构建再次通过。
独立证据：手算均值/布林/ATR、公开 Wilder RSI 价格序列首值70.464135、单位斜率序列 MACD=7；参数非法、零量、平价、UTC session 重置、混合/缺口/重复/未闭合、as_of/prefix invariance、流式批量一致以及真实数据库API边界均覆盖。
真实 Binance 两币种六周期 API 全部成功，返回120–172根已闭合指标行；不是模拟数据。Chromium验证了指标API和实际SMA颜色像素绘制/显隐、既有终端交互、断线恢复、移动端；无页面JS错误。最终30秒浏览器回归：帧间隔P95 16.8ms，299个订单簿更新样本P95 54ms，无观察到long tasks。测量边界沿用Phase3报告，不宣称生产/长时验收。

## 下一切片与限制
RSI/ATR/MACD和确认点已有API结果，独立pane及pivot图上标注待完成；regime仅实现简单close/EMA/SMA关系，不是完整分类器。接下来验证更完整结构定义、break of structure/确认时点、指标pane与参数/窗口交互，再评估Phase5回测依赖。
递归指标seed限定请求窗口，改变limit影响数值；API明确返回seed_start/seed_policy。晚到/修订数据的ingestion-time历史快照未实现，当前只有bar-close as_of约束。VWAP是OHLCV近似，不冒充逐笔价格。交易始终关闭；未实现回测、策略、订单、Paper/Live及AI。

第二切片已实现RSI/MACD/ATR独立pane、周期/窗口控制、确认柱结构标记、BOS/CHOCH严格闭合价首次突破；真实浏览器参数/pane创建删除通过，结构prefix测试通过。完整regime仍待实现，不宣称Phase4全部完成；当前测试总数83，含后续回测用例。
