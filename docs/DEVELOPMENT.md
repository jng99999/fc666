# 本地开发与云任务启动

工作目录 `/workspace/fc666`；已有隔离 checkout，不额外创建 worktree。
要求 Python 3.12、uv、Node 24（Next 要求至少 20.9）、npm、Docker/Compose 与本机 daemon。开发只绑定 127.0.0.1。环境不暴露 localhost 用户预览链接。

## 安装
```bash
cd /workspace/fc666
bash scripts/install.sh
```
停止本脚本拥有的 API/web/research/market/paper 后冻结安装 uv.lock/package-lock.json，构建 web；完成后需运行 start.sh。缓存使用 /workspace/.cache，NEXT_TELEMETRY_DISABLED=1。本地 .env 只在不存在时以 0600 和随机数据库密码生成；不打印、不提交；已存在配置永不覆盖。

## 启动与检查
```bash
bash scripts/start.sh
python3 scripts/dev_services.py status
python3 scripts/dev_services.py stop
bash scripts/check.sh
bash scripts/start.sh
```
启动 TimescaleDB/Redis，等待健康，执行 Alembic upgrade，再启动 API、生产 web、research、market 和 paper worker。status 实际请求 API 和 web 的 readiness 链路；ready 表示工程依赖就绪，交易始终关闭；行情健康另看 /api/v1/market/status，不能用工程 readiness 替代行情有效性。
check 运行真实数据库/Redis integration 和模型测试、alembic check、前端 typecheck/build。测试创建并销毁唯一 fc666_test_* 数据库，不 downgrade 开发数据库。
生产构建前先停止 web，避免读写同一 .next；重新安装脚本只作用依赖/构建及未存在的本地配置。首次无 .next 类型时先 build 再 typecheck。

## 停止与开发
```bash
.venv/bin/python scripts/dev_services.py stop
```
只停止本脚本拥有的 API/web/research/market/paper 进程，不停其他进程、不删除数据库 volume。需要关基础服务时使用同一个 compose 文件的 stop，不用 down -v。
开发 API：`.venv/bin/python -m uvicorn apps.api.main:create_app --factory --reload --host 127.0.0.1 --port 8000`。
开发前端：在 apps/web 执行 `NEXT_TELEMETRY_DISABLED=1 npm run dev`。启动手动进程前先 stop 管理进程，避免端口冲突。

## 诊断
日志 `.runtime/api.log`、`.runtime/web.log`、`.runtime/market.log`；secret 不入日志。数据库用 .env 的 DATABASE_URL，Redis 用 REDIS_URL；API 工厂启动时加载设置。前端服务器代理使用 API_BASE_URL（默认本机 8000），浏览器只访问同源 API 与 /stream/v1/market。
LIVE_TRADING=true 或 TRADING_MODE=LIVE 会使设置校验失败。已实现内置EMA/SMA与历史Paper最低买入限制；private orders、完整Risk/OMS和Live未实现，不能通过环境变量启用实盘交易。
云网络保留 session proxy/TLS/CA，Docker 此阶段容器不访问外网（只有 daemon 拉镜像）。后续包含网络 build/run 时须按 runtime 技能挂载 CA，不禁用验证。

## 行情与浏览器验证
公共现货无需交易所密钥。保留代理和 CA，REST 使用 data-api.binance.vision，WS 使用 data-stream.binance.vision。worker 启动/重连会补齐最近 120 根已关闭 K 线，再建立有效订单簿；缺口或序列断裂不能冒充健康。
```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.import_market --bars 120
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_terminal
```
浏览器验证要求服务已启动和真实公共行情可达，会短暂停止自己拥有的 market worker 并在 finally 恢复。输出和截图位于忽略的 .runtime。浏览器通过锁定的 @sparticuz/chromium npm 包安装，由 scripts/browser_path.mjs 解压；Playwright 使用该可执行文件，无需额外 CDN 下载。保留 TLS 和 npm 完整性校验。此 Chromium 只用于隔离的自动化测试。
性能结果与限制见 PHASE_3_REPORT.md；合成数据只在隔离图表 benchmark 使用，不注入产品行情。

## 回测研究验证
```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_research
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.reproduce_backtest .runtime/browser-backtest-export.json
```
浏览器研究测试使用真实数据库历史并下载完整结果；注入非法费率仅用于验证错误路径。离线复现不需要网络或数据库。研究页仅模拟，不开启下单。

## 后台研究验证
```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_jobs
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.reproduce_backtest .runtime/browser-job-export.json
```
start.sh执行最新0007 schema并启动独立研究worker；研究health在/api/v1/research/status，进程日志.runtime/research.log。停止/恢复只作用自己拥有的进程，死任务租约30秒后可重领，结果与快照保存在PG。详细合同RESEARCH_JOBS.md；确认的官方REST修正保留candle_revisions审计，禁止用普通导入覆盖冲突。

参数批次研究验证（真实服务启动后，测试会停止/恢复自己的research进程）：
```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_batches
```
同一机器上会改变worker状态的浏览器套件应顺序执行；契约与范围见RESEARCH_BATCHES.md。

分段研究验证（顺序执行，测试会停止/恢复自己拥有的research进程）：
```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_holdout
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.reproduce_backtest .runtime/browser-holdout-export.json
```
范围与冷启动边界见HOLDOUT_RESEARCH.md。安装、最新0007 schema和五服务启动保持现有流程。

## 历史回放验证
start.sh自动迁移到最新0007（保留0005的replay_sessions），无需新服务或密钥。
```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_replay
```
回放页/replay仅显示已到达前缀；协议与限制见REPLAY.md。不要增加完整快照/未来数据端点来绕过控制。

回放策略与下载验证：

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_replay_decisions
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.verify_replay_prefix .runtime/browser-replay-decision-prefix.json
```

前缀核对仅验证已到达内容的内部一致性，详见REPLAY_DECISIONS.md。

## 历史 Paper 验证

start.sh自动迁移到最新0007，保留0006的paper_sessions及已有回放、研究与行情数据，新增paper_streams/paper_workers。历史Paper本身不增加进程；实时Paper增加独立paper worker，无新依赖、密钥或域名。/health/ready验证Paper三张表与0007 head。

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_paper
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.verify_paper_prefix .runtime/browser-paper-prefix.json
```

/paper只访问自身前缀API；真实历史驱动，当前不订阅实时行情。资金/库存、风险限额和恢复边界见PAPER_TRADING.md。

## 实时闭合柱 Paper 验证

start.sh升级0007并启动独立apps.worker.paper，第五个进程。/api/v1/paper/status验证其心跳，行情有效性与每账户feed另查。日志.runtime/paper.log。

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m tests.browser_paper_streams
UV_CACHE_DIR=/workspace/.cache/uv uv run --frozen python -m scripts.verify_paper_stream .runtime/browser-paper-stream.json
```

浏览器等待真实一分钟闭合柱，并短暂停止/恢复自己拥有的Paper worker验证断线；与其他会改变worker状态的套件顺序执行。契约及上限见REALTIME_PAPER.md。测试临时数据库删除前关闭自身新连接入口，避免扩展/后台重连与清理竞争，不改开发库。
