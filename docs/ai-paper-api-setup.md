# AI自动模拟接入与验收

本版本启用的是独立AI模拟账户执行链；实盘始终关闭。旧MANUAL/SMA账户、旧现货连续模拟草稿与PPO草稿不会被自动接管。研究页单次研究仍为只读；只有AI_RESEARCH账户明确START后，服务器才可能把有效AUTO建议送入paper账本。

## 用户流程

1. 在 `/paper/directional` 创建AI_RESEARCH独立模拟账户，配置初始资金、数量/金额上限、手续费、滑点和新闻窗口。每次AI开仓必须返回明确数量与止盈止损。
2. 在 `/research/ai` 选择这个账户，记录账户UUID，查看模型/新闻/历史状态。START只是启用运行意愿，缺配置时显示等待，不伪造AI建议。
3. 运营者在服务器配置下列API与授权账户。密钥只放服务器受保护配置文件，不放浏览器、Git或聊天。
4. 配置后重新创建对应服务容器（单进程），在页面确认状态。模型是否有效仍需首次实际请求验证；有错误保持拒绝。
5. 自动研究至少间隔300秒；HOLD不下单，LONG/SHORT需通过当前行情、账户版本、新闻、数量、资金和保护价格校验，FLAT仅减仓。ACCEPTED代表等待后续独立报价，只有FILLED才是模拟成交。
6. PAUSE可恢复，撤销未成交建议并使旧模型回复失效，持仓保护继续。账户STOP永久停止新风险，保留账本与持仓保护。

## 服务器配置

在现有独立测试服务目录 `/opt/fc666-directional-test/config/`：

- `research-worker.env`：`FC666_TA_PROVIDER`、`FC666_TA_MODEL`、`FC666_TA_API_KEY`，可选`FC666_TA_BASE_URL`。上游provider registry决定支持的供应商；OpenAI兼容服务一般使用provider=openai并显式配置base URL和真实模型名，不自动猜模型。
- `directional-ai.env`：`FC666_TA_ACCOUNT_IDS`为授权账户UUID的JSON数组，最多20个。公共创建账户不等于授权模型开销。
- 两文件预置的`FC666_TA_RUNNER_TOKEN`必须一致，保留服务器生成值；`FC666_TA_RUNNER_URL=http://research-worker:8000`是私有容器地址，worker不映射公网端口。
- `LIVE_TRADING=false`不可改变；worker与账户服务不包含实盘连接器。

部署命令需要原compose及新override同时提供：

```sh
docker compose --project-name fc666-directional-test \
  -f /opt/fc666-directional-test/release/infrastructure/directional.compose.yaml \
  -f <本次已验收源码目录>/infrastructure/ai-paper.override.yaml up -d
```

更新配置只重建需变更服务，保留数据卷；不得为加载API清理或重置数据库。

## 新闻日历API

在`directional-ai.env`设置`DIRECTIONAL_NEWS_SOURCE`、`DIRECTIONAL_NEWS_URL`（HTTPS），可选`DIRECTIONAL_NEWS_TOKEN`。GET会带`instrument`参数及可选Bearer。

必须由适配器返回如下语义的JSON：

```text
source: 与DIRECTIONAL_NEWS_SOURCE完全一致的来源名
observed_at: 供应商数据观察时间，带时区ISO字符串
coverage_start / coverage_end: 真实覆盖区间，带时区ISO字符串
events: [{event_id, instruments:["XAU/USD"或"BTCUSDT"或"*"], impact:"HIGH"|"MEDIUM"|"LOW", scheduled_at, known_at}]
```

空events只能表示已核实的覆盖区间没有事件，不能在源故障时返回空数组并重打当前时间。120秒新闻新鲜度、账户指定前后窗口、来源与覆盖校验由服务器执行。未接入、过期、未来、覆盖不足、高影响事件均拒绝新增风险；已有保护退出和合法FLAT继续。

HTTP超时5秒，最大2MB，不跟随重定向；不记录Bearer或供应商响应体。

## 黄金历史API

Swissquote当前公共接口只提供bid/ask，本版本不会把GC=F期货或本地稀疏采样冒充黄金真实历史。未配置黄金历史源时，AI自动新开仓显示HISTORY_UNAVAILABLE。

服务器设置`FC666_GOLD_HISTORY_URL`（HTTPS）、`FC666_GOLD_HISTORY_SOURCE`，可选`FC666_GOLD_HISTORY_TOKEN`。请求GET参数为instrument=XAU/USD、interval=1m、limit=200、end_time=当前毫秒；可选Bearer。

返回统一契约：

```text
{instrument:"XAU/USD", product_type:"SPOT", candle_source:<配置来源原值>, currency:"USD",
 bars:[{open_time:<整型epoch毫秒>,open:<十进制字符串>,high:<字符串>,low:<字符串>,close:<字符串>,volume:<字符串或null>}]}
```

至少100根、最多200根连续已闭合1分钟条；未来/未闭合条排除，冲突、缺口和过期拒绝。返回价格型EMA20、Wilder RSI14/ATR14；volume=null不伪造成交量。历史源与Swissquote当前报价独立显示，跨源基差未验证，不把READY或回测通过等同于实盘资格。

Bitget现货历史自动使用固定Bitget公共接口，保留原产品、K线与订单簿不同来源，不需要密钥。

## 持久性与费用边界

研究与自动控制各用独立SQLite，不迁移旧账户表。每个研究建议绑定快照hash、账户版本、控制epoch和期限；暂停、停止或账户变化后旧回复不能开仓。模型请求最多9次、每次输出1200token、SDK不重试、总期限90秒；这是调用与输出预算，不是金额保证。

研究任务中断不自动重发付费请求。paper提交使用研究job UUID作固定幂等键；回执写入中断后恢复原Proposal时间，不能重置有效期再下单。只在建议剩余有效期覆盖paper决策TTL时提交，成交前再次校验新闻。

自动研究只保留最近100个已结束研究记录/浏览器，手动研究最多100条；账户操作和成交证据仍由独立不可变账本保留。应导出重要研究记录，不能把短期研究历史当永久模型审计库。

## 接入API后的必需验证

本版本代码测试使用明确合成模型、新闻和行情，不证明真实模型质量或盈利。接入真实API后还需核对：真实模型名、错误/超时/限额、新闻更新时间/覆盖、黄金原市场数据身份及基差、一次真实模型建议到paper订单回执、持续运行和重启恢复、费用及历史成本验证。未完成这些检查前不启用实盘。
