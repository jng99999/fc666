# Phase 4/5 分析与分类切片报告

日期：2026-10-05（Asia/Shanghai）。本轮新增完整交易/成本/回撤与风险收益分析、可解释的因果市场状态，未改变现货撮合能力边界。

已实现：v2逐笔滑点/取整成本、realized PnL、完整多次退出交易、胜率/Profit Factor/expectancy、持仓比例、回撤序列、CAGR/Sharpe/Sortino/Calmar；规则和不可用语义见ANALYTICS.md。界面增加风险分析与完整交易表、回撤图；指标区显示历史ER/ATR分类。旧v1导出仍离线复现，新v2导出包含analysis与market_states。
验证：93项后端测试通过，包含手算±收益、样本标准差/下行RMS、零波动/无下行、部分退出合并、多笔胜负、滑点、旧版本冻结/实际v1导出复现、市场分类预热/方向/prefix。Alembic无差异，TypeScript与production build通过。真实浏览器研究验证v2分析/回撤、市场分类、完整下载复现、固定截止一致、错误清理与移动端，无JS错误。终端回归结果另保留.runtime/browser-report.json。

仍待办：独立事件/向量对照、limit/stop/SLTP歧义、衍生品、historical rules、tick级流动性、完整/可校准多周期regime、用户策略任务、Paper/Risk/OMS/Live。最新分析是描述性研究，不是实盘准备度或利润保证。

终端短时回归：117个订单簿更新样本，received-to-RAF P95 53ms，frame P95 16.7ms，未观测到long tasks。期间ETH公共流有失效/重连，缓存按规则拒绝旧值并自动恢复；当前API/网页/数据库/Redis及行情检查恢复健康。这不是连续无故障或长时负载验收。
