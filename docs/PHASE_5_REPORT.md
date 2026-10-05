# Phase 5 Spot 回测首片

日期：2026-10-05（Asia/Shanghai）。状态PARTIAL；已实现可复现的Spot long/flat EMA基线和只读研究页面，不宣称完整回测平台或Paper/Live。

## 已交付
- Typed StrategyContext/TargetSignal及确定性EMA Long/Flat基线，不允许策略直接下单。
- 最多1000根连续finalized bars；闭合柱决定，下一柱open模拟成交；最后信号没有下一柱则不成交。
- Decimal60位局部运算：现金、库存、成本、quote费用、已实现/未实现PnL；tick向不利方向取整、quantity step、最小量/名义额；禁止负现金/负库存，检查PnL守恒。
- 开盘成交容量仅使用上一已闭合柱volume × participation研究代理，不读取当柱未来volume；部分成交剩余IOC取消，持仓不重复加仓，减仓可以后续继续。
- 版本/config/instrument/data SHA256/run_id；导出包含实际dataset、完整账本、equity/orders/fills/signals/假设；reproduce_backtest纯离线逐项核对全部结果并检测篡改。
- 真实TimescaleDB `/api/v1/research/backtest`，显式参数校验和缺口拒绝；研究页面参数、权益曲线、成交列表、错误清理、清单下载。

## 验证
全套后端83项通过（有一项上游Starlette/httpx弃用warning）；迁移一致性、TypeScript、production build通过。手算下一开盘账本：1000初始资金、14买入71.428、9卖出，期末642.860，PnL -357.140。费用/滑点、容量部分成交/无流动性、prefix、未来数据不改变既往fills/equity、非法参数/不支持perpetual、导出篡改及真实数据库接口重复一致均覆盖。
真实浏览器：RSI/MACD/ATR pane创建/移除、均线/窗口参数路径；真实历史回测、固定cutoff重跑相同run_id、下载文件完整离线复现；非法费率清除旧成功结果并展示错误；移动端不横向溢出，页面无JS错误。证据 .runtime/research-browser-report.json、browser-backtest-export.json、research-desktop.png/terminal-indicators.png，全部忽略不作为源码或伪产品数据。

## 边界与待办
单策略同步bounded研究任务，无用户策略执行/后台队列/取消/持久run存储。仅Spot Market Long/Flat；不支持short、margin、limit/stop、funding、historical instrument rule reconstruction。容量代理不是真实开盘流动性，bar-open+不利滑点不是逐笔/盘口精确撮合；假设写入manifest。不强制结束清仓，末仓用close标记。Sharpe/annualization/更完整trade分析保持null+reason，不能把fill count叫完整trade count。
未完成向量/事件引擎独立对照、SL/TP歧义、stop/limit/latency、衍生品、策略Lab、Risk/OMS、Paper/Live、AI。后续先完善结构/regime和撮合语义，再扩展事件/向量对照；不凭收益上线交易。
