# Phase 1 验收报告

2026-10-05。当前目标：工程基础、Instrument/Candle 与真实服务启动；已完成该范围。完整交易平台未实现。

## 交付
- Python 3.12/FastAPI/Pydantic v2/SQLAlchemy 2/Alembic/psycopg/Redis，uv.lock 固定解析版本。
- Next.js 16.3.8、React 19.2.4、TypeScript，package-lock.json 固定依赖。前端真实查询服务端 health，不填假行情/收益。
- Docker Compose：TimescaleDB 2.26.0/Pg17、Redis 7.4.7，以镜像 digest 固定；数据库持久 volume，端口 loopback。
- Instrument/Candle 模型：精确 Decimal，拒绝 binary float/NaN/非法精度，UTC aware timestamps、标准周期/市场、OHLC 校验；数据库唯一/foreign key/check constraints。
- Alembic 0001 创建基础表和 TimescaleDB hypertable，回退仅删除本模块表，不删除可能共享 extension。
- API /health/live、/health/ready、/api/v1/system/status；readiness 检查真实数据库、目标表/迁移和 Redis，异常返回 503。不把工程 ready 当作可以交易。
- 可重复 install/start/check 与仅停止自有进程的管理器；PID starttime 防误杀。随机本地数据库密码只存在 .env，权限 0600、Git ignored，生成器保留已有配置。

## 验证证据
- `bash scripts/check.sh`：22 passed，0 failed/skip；一个 Starlette/httpx 上游弃用警告（测试有效，无隐藏忽略）。测试分别创建临时真实 PostgreSQL 数据库并清理，不污染开发账本。
- upgrade -> downgrade -> upgrade -> repeated upgrade；Timescale hypertable 存在；alembic check：No new upgrade operations detected。
- 首次失败定位为 Timescale 自动 time index 与 ORM metadata 不一致，补齐 ORM index 后通过，未删除检查。
- Decimal 18 位小数 roundtrip；重复 candle 和负 tick 被数据库拒绝；数据库断开、Redis 断开、未迁移 schema 的 API readiness 为 503；Live 配置拒绝，live-orders 路由 404。
- 前端 typecheck/production build 通过；npm audit --omit=dev：0 vulnerabilities（只代表本次 registry audit 结果）。
- API/web 启动并功能请求通过，web -> API -> PostgreSQL/Redis 确认；网页响应包含工程阶段、Not Implemented、实盘关闭。停止/安装/重启与重复 start 均已验证；重复启动复用自有健康进程。
- 单机 loopback readiness smoke：30 请求、并发 1，p50 2.50ms、p95 4.85ms、max 13.58ms；不是负载 benchmark，不推断生产 API/行情/UI 性能。
- Git whitespace 检查通过；.env/.venv/node_modules/.next/.runtime 均 ignored。

## 审查与限制
所有新路由只公开工程状态，不提供用户账户或交易信息；接入私有数据前必须增加认证授权。开发 PostgreSQL 用户拥有本地数据库管理权限，方便 migration/test，不能作为生产最小权限配置。
没有浏览器交互/E2E/视觉回归，没有行情/订单/仓位/风控/AI 应用实现；只实现 core 数据首切片，不宣称全部 DATA_MODEL 完成。Tailwind/shadcn/chart/store 工具在 Phase 3 按功能需求引入，不安装无用途的空壳依赖。
Phase 0 完整竞品交互研究保留待办。下一目标 Phase 2：公共真实行情与 adapter 恢复测试，详见 [DELIVERY_PLAN](DELIVERY_PLAN.md)。

## 可复用配置
安装与启动命令已确认保存为 install_script/start_skill 草稿；草稿保存不代表发布或全新任务恢复已验证。当前 native 应用与 Compose 数据服务经过验证；完整应用容器部署留待后续 infrastructure 工作，不声称已验证。
