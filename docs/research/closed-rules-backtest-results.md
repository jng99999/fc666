# 闭合规则固定历史验证

冻结协议见 `../closed-rules-backtest-protocol.json`；研究 ID：
`4a9cf2a8198e5971065deb62de5b6bc764fd1aaa601ad070c6d918afcfd25eec`。

## 实际运行范围

- BTC/ETH 各 1h、4h，共 8,000 根真实 Bitget 闭合柱。1h 为 2026-08-23 16:00 UTC 起，4h 为 2025-12-16 16:00 UTC 起，均至 2026-11-15 00:00 UTC（结束不含）。每个 2,000 柱全区间另设固定后 800 柱时间分段，各自独立资金和预热。
- 黄金固定 2025-01 HistData 配对 tick：原 ZIP 40,095,748 bytes、CSV 260,920,170 bytes，5,798,226 条报价。形成 30,045 根合格 1m，排除 152 个覆盖不足分钟。按时间取首、尾各 2,000 柱，保留 13 / 2 个缺口边界，由规则重新预热。
- 三规则 × 两种成本 × 两种新闻政策，共 120 案例、10 个数据窗口。原始输入、完整成交/权益/新闻覆盖收据保存在任务 `outputs/price-action-paper-20261115/closed-rule-study/`，前端仅发布固定摘要与协议，不含原始 tick、私人账户或密钥。

## 结果

实际命令：

```text
python3.11 -m scripts.study_closed_rules --history <frozen-crypto> --gold-history <frozen-gold-1m> --output <isolated-results>
{"study_id":"4a9cf2a8198e5971065deb62de5b6bc764fd1aaa601ad070c6d918afcfd25eec","cases":120,"strict_fills":0,"gold":"AVAILABLE"}
```

60 组 `REQUIRE_HISTORICAL` 缺少真实历史新闻，全部零成交。另 60 组显式 `UNCOVERED_RESEARCH` 诊断：8 组正收益、50 组负收益、2 组无收益变化。不同产品数量不同，不能混排为共享资金组合，也不能只挑正收益案例。

同一 BTC 1h 全区间结构/FVG，独立 10,000 USDT、固定 0.01 BTC：基准成本净盈亏 **+1.4561025 USDT**；压力成本 **−33.9051072 USDT**。手续费与成本足以改变符号，结果不能支持“稳定盈利”。全部案例均保持 `live_eligible=false`、`historical_news_verified=false`、`trading_enabled=false`。

## 实际复现与反例

服务器 Python **3.12.15** 在无网络、无线上账本挂载的隔离容器中，重跑真实 BTC 1h、2,000 柱趋势回撤基准诊断（112 笔模型成交），完整收据逐字段与本机 3.11 结果一致：

```text
verify_result(Path('/checks/verify-btc-real.json'))
{"verified_run":"7708675f0e0595e58edc9f02fd60d0541fa20df4bd99b785ecba1f447fa8194f","full_receipt_equal":true,"trading_enabled":false}
```

源码测试覆盖 next-open、冻结保护价、同柱双触发先止损、不利跳空、点差方向、反向先退出、缺柱取消、费用守恒、缺新闻严格拒绝、已知 HIGH 在诊断中仍拒绝。已修复 Decimal 数值相等但表示不同导致缓存证据串用的问题，缓存身份绑定完整数据摘要；冷/热完整收据一致。离线脚本重复同一输出目录保留原生成时间，不覆盖不同输入结果。

## 必须保留的限制

OHLC 是模型价格基准，完整点差和单侧滑点是明确假设，不是历史实际盘口或真实 tick 成交。回撤按收盘标记权益计算；期末不强平，未平仓没有扣未来退出费用。没有融资、资金费、借贷、杠杆强平或深度容量模型。

黄金 HistData 的完整 tick 聚合与 Swissquote 每 10 秒抽样来源和密度不同，不能据此声称同源实时前向表现。历史真实新闻未取得；严格模式零成交与合成 HIGH 场景只证明门禁机制，不能证明历史消息覆盖。固定后 40% 分段可见且可重复研究，不是密封盲测。
