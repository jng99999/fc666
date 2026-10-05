# Phase 2 Spot 验收报告

2026-10-05。公共 Binance Spot 数据切片已实现并验证；衍生品 Phase 2b 与完整策略事件总线未实现。

## 功能
只读公共适配器使用 data-api.binance.vision / data-stream.binance.vision，无密钥、无私有接口、无下单方法。BTCUSDT/ETHUSDT 映射为标准 instrument_id；tick/step/minimum 由 exchangeInfo 获取。
六周期 1m/5m/15m/1h/4h/1d 的已闭合 K 线分页导入；UTC 对齐、exclusive close_time、Decimal 精度、OHLC/volume 验证。重复导入幂等；已闭合 bar 发生冲突时拒绝并要求显式 revision，不静默覆盖。
迁移 0002 在 DB 强制时间周期与 UTC 对齐；原有 0001 支持回退再迁移，alembic check 无差异。
常驻 market worker 每币种一个公共 WS，缓冲 depth delta 期间取 REST snapshot；U/u 断裂、错误/交叉/不完整盘口、队列/内存超限、stale 数据均 invalid 并重连/resnapshot。重新连接有新 generation，重复/out-of-order 已覆盖数据忽略；心跳/Pong 使用 websocket 客户端标准实现。
缓存 TTL 15s；断线主动清除各 channel；ticker/book/trade 与六周期 kline 以版本化 latest snapshot 提供，最近成交有界 100 条；closed kline 写入数据库。
HTTP instruments/candles/market status/snapshot 与本地 WS subscribe/unsubscribe gateway 实现；查询限制 1000、UTC 范围与 symbol/interval 白名单；无数据明确 503/empty。UI cache 不作为可靠策略输入或审计存储，不假装已建立 durable event bus。

## 验收证据
- 真实历史导入：2 symbols × 6 intervals × 120 bars = 1440 finalized candles；每组 missing_count=0。冻结截止时间 1791211200000 重跑：12 组全部 inserted=0 / gaps=0；dataset SHA256 与来源/截止时间保存在 .runtime/import_report.json。
- 公共交易所 WSS 收到真实 trade/depthUpdate；本地 gateway 同时验证两币种 ticker/book/trade 六个 snapshot 通道。
- worker 停止：market/status unavailable，工程 API readiness 仍 ready；重启两币种恢复 healthy，book generation 改变。
- Binance exchangeInfo 首次 HTTP400 原因是 symbols JSON 参数含空格；修复 compact JSON，并测试精确编码，不换假接口。
- 后端 51 项测试通过，0 failed/skipped；覆盖分页/未闭合 bar/缺口、规则归一化、rate-limit Retry-After、拒绝 HTTP、Decimal/UTC、DB 约束、幂等冲突、book gap/invalid/crossed/memory-bound、重连 generation、WS ack/unavailable/unsubscribe、基础依赖失效。
- typecheck/build/迁移一致性通过；web 同源代理实际返回 healthy status、真实 ticker 与 120 根 ETH 历史条目。

## 限制与后续
导入和 WS 流只覆盖公共 Spot；没有 funding/OI/mark/index/basis、私有账户或交易。当前 REST limiter 按进程保守 1 request/s，并遵守 418/429 Retry-After，不是分布式 weighted limiter。行情 history snapshots 最多每侧 1000 档，不声称完整流动性；长期 retention/分布式采集与 durable bus 后续按需求验收。
没有实际交易所故障注入；断线/gap 故障通过协议 fixture 与自有 worker 重启测试验证。真实数据 smoke 不能替代生产可靠性验收。
后续 Phase 3 接入专业终端，Phase 4 开始指标；Live 始终关闭。

补充：worker 在启动/重连前检查并补齐每周期最近 120 根 finalized bars；缺口必须先修复，已有完整数据避免重复 REST 请求。当前全套后端测试 52 项通过。
