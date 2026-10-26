# 量化、黄金、加密与 Agent Skill 开源候选

核验日期2026-10-27。通过 GitHub 连接器读取仓库元数据、当前提交及相关源码/技能/许可证；响应保留于工作区 `outputs/quant-audit-20261027/research/`。候选不是绩效认证，不根据 star 数量启用交易。

|候选及固定提交|实际读到的能力|FC666取舍|
|---|---|---|
|[Freqtrade](https://github.com/freqtrade/freqtrade/tree/dafa0b6f4e1af30a80aedf730b5509ca59d0d7cc) `dafa0b6`|`docs/leverage.md` 明确 spot 无空头，futures另有方向/保证金契约；`docs/stoploss.md` 描述保护退出和成交限制；元数据GPL-3.0|优先参考方向和保护单边界，独立实现本轮quote模拟；没有复制或内嵌其执行引擎|
|[NautilusTrader](https://github.com/nautechsystems/nautilus_trader/tree/04f766b4d434d67fc8672026fefa25135bdc01be) `04f766b`|实际搜索并定位 `crates/common/src/factories/order.rs` 的 bracket 工厂，支持关联入场/SL/TP的订单表达；LGPL-3.0|适合作为后续事件撮合/保护单对照。不是在现货ledger加负库存；本轮没有引入其Rust/Python运行依赖|
|[dukascopy-node](https://github.com/Leo4815162342/dukascopy-node/tree/33a6c6e3de97fab9904afc80a3a2fac8b46d8bfb) `33a6c6e`|README提供tick/历史/实时数据下载入口；MIT|作为黄金历史源交叉验证候选。需分别核对bid/ask、时间、缺口、价格倍率和数据使用条款，不能直接拼接HistData或Swissquote|
|[AI Trader MCP / skills](https://github.com/DaviddTech/ai-trading-agent/tree/fa5d9dfe9f02c281c314278edc16bb1527abc670) `fa5d9df`|实际读取 `skills/ai-hedge-fund/SKILL.md` 与README；研究/基准/跨产品周期/失败报告工作流，依赖Trader Dev MCP；MIT|吸收固定基准后再优化、保留失败与前向验证的研究约束。当前未连接Trader Dev，不宣称安装后即可下单|
|[crypto-skills](https://github.com/0xrikt/crypto-skills/tree/360c5e24d6ee689f21491771781dbd70ba2034e9) `360c5e2`|实际读取 `crypto-backtest/SKILL.md`，明确只支持spot long-only；含通用阈值建议；仓库许可证未确认|可作为研究提问清单，不能补足空头，也不直接采用其未经本数据验证的固定阈值或复制再分发|
|[quant-trade-skill](https://github.com/aresbit/quant-trade-skill/tree/d70e9f0dea28778685478f26f425505b584dde70) `d70e9f0`|实际读取SKILL.md，涵盖统计套利、做市、组合风险等知识框架；LICENSE路径404、元数据未确认许可|仅作为研究索引；文档不是可执行策略，也没有交易记录可验证，不自动安装运行|
|[awesome-quant-skills](https://github.com/JerryLiu369/awesome-quant-skills/tree/587ceba42c17162f1316518554109b6cc03ae5b8) `587ceba`|实际读取NOTICE；目录自身MIT，但镜像skill保留上游许可证，部分只有SKILL.md而没有配套脚本|用于发现候选，逐个回到上游审核，不将整目录当统一MIT包导入|
|[vectorbt](https://github.com/polakowo/vectorbt/tree/8e30b8ce102f302bec0c0226e05b59714453bebd) `8e30b8c`|实际读取LICENSE.md，Apache2.0附Commons Clause限制|暂不作为商业平台的内嵌批量回测依赖；现有Decimal因果回测保留，批量性能需求另行评估|

## 本轮确实补进源码的内容

- 两类现货模拟引擎完整OHLC上下文，内部8策略进入真实模拟下单函数并验证账本。
- 新的独立双向quote研究内核、SL/TP、新闻必须源故障禁入、接收时刻与报价时刻的防回填约束。
- 可重放的真实成对黄金历史机械研究脚本，保留亏损年度，不进行参数寻优。
- Hyperliquid公开账户发现与成交观察器。来源为[官方info文档](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)，没有API Key、私钥、下单或跟单接口。

外部skill作为待验证研究资料，尚未安装至全局代理环境。研究资料中的提示词不能改变FC666实盘关闭、来源真实性、隔离账户或旧账本保护边界。
