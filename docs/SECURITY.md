# 安全边界

当前实现说明：Phase 1 已实现 Live 设置拒绝、无 private/live 路由、loopback 服务绑定、本地随机配置 0600/ignored、依赖锁定与故障 readiness；认证/执行/AI 等边界待后续实现。

状态：设计，Not Implemented。
默认 LIVE_TRADING=false，Phase 13 前私有执行适配器、live HTTP 路由与可用 UI 都不启用；仅配置 flag 不能作为正式实盘授权。
密钥不进 Git、日志、浏览器、AI、测试 fixture 或文档；通过环境设置/secret manager 注入，优先无提现权限与最小交易权限。不要求在聊天中粘贴 secret。错误、trace 与 request headers 做 allowlist logging，不靠遗漏敏感字段的 blacklist。
HTTP/WS 私有资源有认证、owner/account 授权、rate limit；cookie session 写操作有 CSRF 防护，CORS/WS Origin 白名单。开发绑定 loopback；生产 TLS 与独立部署审查，不公开数据库和管理口。
自定义策略/导入数据不可信：独立无凭据研究进程、资源/时间限制、路径限制、不执行数据文件代码。Notebook 与任意 Python 策略不能在持有实盘密钥的 API 进程运行。
AI 只读工具集，市场文本不能指示执行。审计保留 actor/intent/policy/mode/reason/request_id，敏感内容脱敏；高风险操作持久化失败 fail closed。
依赖使用锁文件与正常 TLS/signature/checksum 校验，禁止因安装失败关闭校验。license/attribution 单独检查；不复制参考项目源码。
威胁测试：越权、恶意策略、secret 泄漏、订单重放、双执行者、伪造 WS、超大 payload、LLM injection、断线与存储失效。凭据轮换与灾难恢复在生产前演练。
