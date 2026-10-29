# TradingAgents 独立研究接入

本次用户已确认研究报告提出的接入方向：保留 FC666 行情与执行身份，TradingAgents 只生成研究建议，严格适配后检查新闻/风险，先旁路观察。实现边界是 `SHADOW_ONLY`：没有提交账户决策的路由或代码调用，不启用实盘，不接管旧账户。

## 已实现

- 网页 `/research/ai`，策略研究页内有入口，复用既有导航、自定义选择器和卡片样式。
- 选择本浏览器独立双向模拟账户，保存真实服务器快照并下载JSON；模型未配置也可验证该路径。
- 冻结账户版本、原品种/报价源、真实bid/ask、最多20条独立报价、新闻覆盖摘要及原始新闻hash。明确未提供闭合K线/OHLCV指标、社交和基本面；报价不冒充K线。
- `core/research/tradingagents.py` 定义精确十进制候选、8角色报告、版本与快照绑定，并只读校验数量/tick/保护价格、资金、最新报价、当前账户状态及真实新闻门禁。HOLD/FLAT/开多/开空语义明确，不能从上游Sell猜测开空。
- 研究SQLite为账户文件同目录的 `tradingagents.research.sqlite3`，不会向账户表加字段。每浏览器最多100条历史，展示20条；模型任务全局最多2个排队/运行，每浏览器1个。相同请求ID重发返回同一冻结任务，并发不会重复调用。
- 研究线程与报价/保护线程分离。重启将RUNNING标记INTERRUPTED，避免不确定模型请求自动再次计费；QUEUED未调用任务可以正常继续。
- `apps/tradingagents_worker.py` 真实复用固定上游v0.7.1提交 `c4544913a14ed3df5f1ed7b9e8483270ce20682a` 的8个决策角色工厂；不运行上游默认Yahoo取数/别名转换/公司身份查询和工具图。数据报告来自FC666快照，随后第9次严格输出FC666候选。
- 每角色只允许一次调用。上游结构化降级尝试会被拒绝；全任务最多9次模型调用，每次输出上限1200 token、SDK不重试、单次超时15秒，父进程总期限90秒。这里限制的是调用和输出，不是未经测量的实际金额保证。模型价格仍取决于所选供应商。
- 私有worker使用服务器间Bearer认证，后台允许账户清单控制模型开销；网页看不到模型密钥。子进程只继承必要运行变量与专门模型配置，在新临时目录执行，避免上游自动寻找项目.env。
- 研究完成保存原建议与“完成时”校验。即使`SHADOW_READY`也不下单；时间/账户变化后不能沿用该校验。要将来驱动模拟，仍需新的当前证据验收和明确执行接入。

## 可选运行环境

上游依赖较多，因此 `infrastructure/tradingagents-requirements.txt` 只用于独立worker，不加入账户服务主依赖或镜像。本地集成测试已在专用venv安装这个固定Git提交。版本与PEP610安装来源都要匹配，不能悄悄升级为其它版本。

账户API仍使用 `apps.api.directional_service:create_app`，单进程单worker，既有数据库和LIVE_TRADING=false不变。新增可选服务器变量：

- `FC666_TA_RUNNER_URL`：私有worker基础地址，必须由运营者配置，不接受网页传入URL。
- `FC666_TA_RUNNER_TOKEN`：至少32字符的随机服务器间密钥，通过服务器安全配置设置，不在聊天中发送。
- `FC666_TA_ACCOUNT_IDS`：授权模型研究的账户UUID JSON数组，最多20个。未授权账户不能产生模型调用。

私有worker还需运营者显式设置：

- `FC666_TA_PROVIDER`、`FC666_TA_MODEL`、`FC666_TA_API_KEY`，可选`FC666_TA_BASE_URL`。
- 不自动选择默认模型；API密钥只进入私有worker进程。上游provider registry选择对应API客户端，目前本适配使用API凭据，不自动进行订阅登录。

示例启动命令（假定运营者已在服务器安全配置变量）：

```sh
python -m pip install -r infrastructure/tradingagents-requirements.txt
uvicorn apps.tradingagents_worker:create_app --factory --host 127.0.0.1 --port 18669 --workers 1
```

生产/测试服务器如使用容器，worker与API的私有可达地址应由运维明确配置，不能把容器内127.0.0.1误当宿主机。此worker端口不应直接暴露公网。模型未选定时不部署带凭据worker，账户API不设置上述runner变量，页面明确显示未配置，仍可保存真实快照。

## 接口

全部在 `/api/v1/directional/research`，复用现有HttpOnly浏览器会话、同源POST检查及8KB命令体限制。

- `GET /status`：SHADOW_ONLY、runner是否配置、固定上游版本；不返回密钥。
- `GET /jobs`：当前浏览器20条研究记录及其已授权账户。
- `GET /jobs/{uuid}`：按所有者读取一条任务。
- `POST /accounts/{uuid}`：`request_id`、`expected_revision`和`mode=PREPARE|ANALYZE`。PREPARE只保存快照；ANALYZE需服务配置、账户授权和新鲜行情。

没有execute/approve/submit交易路由。Next代理显式白名单这些路径，只转发独立会话cookie，不把其它cookie或任意路径送往后端；超时后保留同一请求ID重试，不能自动重发新的付费任务。

## 验证边界

`tests/test_tradingagents_research.py`使用合成模型与新闻，测试严格字段、原市场身份、新闻缺失/过期/覆盖不足/高影响窗口、报价过期、上下文/版本改变、tick/仓位/资金、幂等并发、所有者隔离和恢复，同时核对账户前后完全相同。

`tests/test_tradingagents_worker.py`在真实固定上游依赖环境中运行8角色和第9次适配，使用显式FakeLLM并禁止socket连接，验证没有Yahoo改市场或外部调用。另测试结构化失败禁止fallback、调用预算、认证与无模型配置。该测试不证明真实LLM质量或盈利能力。

前端检查包括同源/路由/不重试代理、SSR按钮在hydration前禁用；真实浏览器需覆盖手机、选择账户、快照保存/下载/刷新、失败态以及研究不改变账户。未配置真实模型时，不能将模型替身结果发布为真实AI建议。

## 当前限制

现有独立账户快照只有20报价，尚未接入闭合K线和指标快照。研究必须承认输入不足，不能用模型编造指标。没有将本次接入称作完整AI自动交易、原视频策略或已通过真实历史成本检验的新策略。

没有模型供应商/模型选择及合格新闻配置时，真实AI建议、费用/延迟及前瞻表现仍未验证。新闻缺失不解除禁入；已有止盈止损继续独立执行。客户端无法保密关键凭据，账户归属、模型授权和风险校验均在服务器决定。
