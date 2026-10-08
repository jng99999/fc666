# FC666

专业 Crypto Quant Workstation，已实现工程基础、Binance Spot 公共行情、只读交易终端、闭合柱历史回放和历史模拟账户：FastAPI、Next.js/TypeScript、PostgreSQL/TimescaleDB、Redis。
终端提供 BTC/USDT、ETH/USDT 六周期真实 K 线、报价、订单簿、成交、布局保存、移动端切换和基础指标 overlays、RSI/MACD/ATR 面板与已确认结构标记。新增EMA/SMA两种内置策略的真实历史现货回测研究页、版本/参数清单、后台任务/取消/恢复、共享快照的2–8组参数批次比较、固定参数样本内/样本外分段研究与完整离线复现导出。完整策略Lab、多类型撮合、生产 OMS、AI 和实盘执行仍 **Not Implemented**。
历史回放提供固定快照、逐根/播放/暂停/速度、刷新恢复、因果指标及固定 EMA/SMA 目标决策；支持已到达前缀下载与离线核对，不含逐笔簿/撮合。

`/paper` 提供独立现货模拟资金/持仓、下一柱开盘成交、手续费/滑点、买入金额与回撤限制、手动停止买入及持久恢复。包含历史账户与独立实时闭合柱 Paper；实盘未开放；详见 [Paper 契约](docs/PAPER_TRADING.md)。
实盘强制关闭，`LIVE_TRADING=true` 或 `TRADING_MODE=LIVE` 会被拒绝。

## 开始开发
要求 Python 3.12、uv、Node 24、npm、可用本机 Docker/Compose。
```bash
cd /workspace/fc666
bash scripts/install.sh
bash scripts/start.sh
python3 scripts/dev_services.py status
```
验证前先停止 API/web，避免构建过程中读写同一前端产物：
```bash
python3 scripts/dev_services.py stop
bash scripts/check.sh
bash scripts/start.sh
```
本地服务仅绑定 loopback：API 8000、web 3000、PostgreSQL 5432、Redis 6379。
首次安装生成 `.env`（0600，随机本地数据库密码），保留已有文件，不提交或输出密钥。

- [当前目标与执行计划](docs/DELIVERY_PLAN.md)
- [Phase 1 验收报告](docs/PHASE_1_REPORT.md)
- [Phase 2 行情报告](docs/PHASE_2_REPORT.md)
- [Phase 3 终端与性能报告](docs/PHASE_3_REPORT.md)
- [Phase 4 指标首批报告](docs/PHASE_4_REPORT.md)
- [Phase 5 回测首片报告](docs/PHASE_5_REPORT.md)
- [风险收益分析与市场分类](docs/PHASE_5_ANALYTICS_REPORT.md)
- [后台研究任务报告](docs/PHASE_6_REPORT.md)
- [自主长任务计划](docs/LONG_TASK_PLAN.md)
- [运行与诊断](docs/DEVELOPMENT.md)
- [产品规格](docs/PRODUCT.md)
- [架构](docs/ARCHITECTURE.md)
- [UI 设计系统](docs/UI_DESIGN_SYSTEM.md)
- [研究记录](docs/RESEARCH.md)
- [完整路线图](docs/ROADMAP.md)

云任务使用现有隔离 checkout，无需额外 worktree。当前为只读研究终端，不具备下单能力。

`/paper/realtime` 已接入公共实时闭合柱、独立模拟 worker、延迟/激活前拒绝、修正/缺口保护及暂停/停止控制。模拟成交采用及时闭合柱收盘参考价，区别于历史下一柱开盘模型；详见 [实时 Paper 契约](docs/REALTIME_PAPER.md)。

五大板块已纳入[统一执行计划](docs/FIVE_BLOCK_EXECUTION_PLAN.md)。当前新增持久化暂存收件箱、请求账户组合敞口、只读私有连接基础、滚动研究与本地备份恢复验证；准确范围及未完成项见[本轮基础能力](docs/FIVE_BLOCK_FOUNDATIONS.md)。
