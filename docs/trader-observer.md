# 公开交易账户观察与学习输入（第一阶段）

这是独立的离线只读采集工具，不连接现有模拟账本、下单接口或AI服务，不持续轮询，不跟单。一次命令最多发现3个公开账户、每个最多3页近期成交；采样成功只说明取得了公开行为证据，不能宣称复原或学会了交易策略。

## 已实现边界

- 官方域名公开榜单：`GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard`。该CDN文件不属于已文档化的稳定API，榜单刷新时效不能由抓取成功推断。响应解码上限64MiB，只保留最多3个选中地址与来源摘要，不保存显示名和整榜地址库。
- 官方只读Info API：`POST https://api.hyperliquid.xyz/info`，固定请求类型 `userFillsByTime`，不用密钥、登录、钱包签名或交易方法。虽然HTTP方法是POST，实际是官方历史查询。禁止将本模块改接 `/exchange` 或复制交易。
- 账户选择是公开榜单中最先出现、当日报告量与账户值为正的唯一地址，用于示例采样；不意味着是人类交易员、排名可靠、有超额收益或适合复制。
- 请求时间为UTC毫秒，结束时刻固定在本次启动时，窗口1小时至7日；每页最多2000条、最多3页、总计最多10次请求，无自动重试。429、超时、HTTP错误、过大响应、坏JSON、未来/范围外成交明确报错，不伪装成空列表。
- 分页用最后时间戳作为inclusive下一页起点，以 `(账户, 原coin标识, tid)` 去重，保留同一毫秒不同成交；同ID金额变更直接拒绝。满页同时间戳无法推进和页数用尽明确标注截断。即使读到API尾部，官方只暴露最近10000笔，仍不能证明整个请求历史完整。
- 每个响应保留URL、请求体、UTC抓取时间、HTTP状态、原始字节SHA256与字节数。成交响应原字节另存 `responses/<sha256>.json`，可直接复核hash。榜单只记录全响应hash和选中行，不保留整个大文件。
- 方向同时检查 `dir`、`side` 与 `startPosition ± sz`。区分开多、平多、开空、平空和跨零反手，不能把所有SELL当作做空。Spot的 `Buy`/`Sell` 或其他未知方向标为UNCLASSIFIED，不从字符串猜测仓位。
- 金额使用Decimal，保留原交易ID、订单ID、交易hash及coin原名。手续费按token分别统计，允许负手续费返佣；`builderFee`另存，避免未经确认重复扣费；来源报告的已实现盈亏按coin分组，不混算成美元账户回报。不会将 `@index`、HIP-3代码或代币映射成黄金XAU/USD。
- 学习输入增加逐coin行为特征：UTC小时分布、原始单位成交规模的nearest-rank分位，以及可核验的完整持仓episode。只有明确从0仓开仓、每笔`startPosition`与上一`derivedEndPosition`连续、最终回到0仓才计算持仓秒数、分批加仓及部分减仓次数，并保存事件ID作为证据。窗口前已有仓位、未闭合尾部、缺失中间成交、未知Spot方向、跨零反手都标unmatched/unknown；不会拆出虚构完整交易或收益率。相同毫秒按精确tradeID确定检查顺序，仍必须满足仓位链才能接受。每coin重复列出样本截断和历史不完整状态。

## 运行和验证

在仓库根目录，用包含现有项目httpx依赖的Python3.12环境运行，不需要安装新依赖：

```sh
LIVE_TRADING=false python -m core.research.trader_observer \
  --output /absolute/new/evidence-directory --traders 3 --hours 24 --max-pages 3
python -m pytest -q tests/test_trader_observer.py
```

输出目录必须不存在，避免覆盖旧证据。主产物 `observation.json` 包含发现来源、原始成交、标准化事件、来源报告费用/盈亏统计、覆盖与截断说明；`responses/` 包含逐页原字节。`trading_enabled`固定false。该CLI尚未接网站或部署服务。

2026-10-27首次实际采样证据在任务的 `outputs/quant-audit-20261027/traders/`：

- 采样启动 `2026-10-26T21:42:05.978Z`，结束 `21:42:28.889Z`。
- 8次公开请求：一次榜单GET与7次Info POST；榜单39,113,670字节、47,591行。
- 三个选中公开账户：一个API空页，另外两个在3页预算中分别取得5,996和5,998条去重成交，共11,994条。二者都明确 `pagination_truncated=true`、`requested_window_complete=false`。
- 请求24小时不等于取得完整24小时。实际可见成交约落在19时至19时54分的局部历史，不能声称覆盖到21时42分或完成实时订阅。榜单、局部成交也不能证明一致的实时状态。
- 样本包含现货、加密永续和HIP-3其他市场；这里只观察原始标识，不伪称全部是加密或黄金交易。
- 首次运行时已记录每页原字节hash；随后离线把保留的原JSON序列化，逐页验证字节长度和SHA256与首次捕获完全相同后保存到responses。`raw-response-integrity.json`记录这一过程，未再次请求网络。之后的CLI直接保存原始响应字节。

## 黄金来源的真实状态

黄金公开交易员 **未接入**。本次核对了 [Myfxbook官方API](https://www.myfxbook.com/api)：history/open-trades需要登录session；没有索要登录信息或尝试绕过。也检查了 [MQL5公开Signals](https://www.mql5.com/en/signals) 和 [官方规则](https://www.mql5.com/en/signals/rules)：公开展示部分账户统计不等于存在已验证、匿名、完整的逐笔成交API。本工具未抓取或声称学到了这些黄金信号。

## 学习模式接下来如何验收

1. 当前阶段生成可回溯的学习输入、方向/费用统计及上述持仓/时段/规模行为事实。成交记录不能揭示触发指标、取消订单、止盈止损、隐藏持仓、外部对冲、新闻过滤或账户完整收益，不能由几笔盈利反推出“完整策略”。
2. 下一阶段在已保存行为事实之上构建待验证假设；所有假设绑定事件ID与覆盖范围，遇到截断保留低置信度。不能把未知方向或缺失仓位强行补为0，不能把短持仓直接断言成某种特定入场指标。
3. 若要验证指标/消息面相关性，需额外对齐当时可获取的市场与新闻时间戳，只用成交之前数据，预先固定假设和留出期。公开记录的幸存者偏差、样本选择偏差和未观测成本必须记录。
4. 假设先转成独立研究规则，在含手续费/点差/滑点的历史及前向模拟中比较基准。没有通过独立验收的候选不能进入自动执行；本观察工具没有自动晋级或学习后改策略机制。
5. 更高频订阅、更多账户、长期存储、黄金授权数据源、AI模型费用及服务部署属于后续独立工作；本阶段不创建调度、不轮询、不发交易指令。实盘持续关闭。

官方接口契约见 [Hyperliquid Info文档](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)，特别是userFillsByTime的2000笔单页、10000笔保留上限和inclusive时间范围。
