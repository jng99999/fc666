# 训练成本干预结果：改善成本适应，尚未证明行情增益

真实实验于 2026-10-22 15:56:48 UTC 冻结，318.70 秒完成。预注册提交 `a817431c355484be9164b967c7041be6c17bc516`；执行源码提交 `153725cf8dd608df55e32e412026e7ef3fe7b7af`。六次训练各 131072 步，共 786432 步，只运行一次；不重新训练旧控制，不挑选种子，不计算新 2025 结果。

**H3 的探索条件满足，但市场增益判据失败，仍未授权模拟接入。** 2024 已被查看，不是盲测或真实前向。三个新 D 模型的原收益/回撤/动作条件在这个探索窗全部满足，不能将此改称原时间外资格或继承旧模型的部署证明。

## 三个种子的压力成本配对结果

| 种子 | 原 B 净收益 | 新 D 净收益 | 提高百分点 | 原 B 显式成本 USD | 新 D 显式成本 USD | 新 D 最大回撤 | 新 D 成交 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 7 | 3.36481545% | 4.13953120% | 0.77471575 | 462.156050 | 137.132840 | 2.54098995% | 694 |
| 19 | -1.29514090% | 2.34111440% | 3.63625530 | 1054.680960 | 206.150360 | 3.15274414% | 790 |
| 42 | 2.60098505% | 4.34152165% | 1.74053660 | 817.267110 | 103.877070 | 2.61572198% | 660 |

全部 D 的三个目标动作计数分别为 `[11,5804,102]`、`[61,5426,430]`、`[0,5491,426]`，每种成本场景均有两种以上动作与真实模拟成交。因此不是用零成交现金策略通过 H3。不过第一种子仍有约 98% 决策选择 25% 目标，不能据此宣称行为多样或预测有效。

H3 要求每个种子均降低压力场景显式费用、提高净收益并保留至少两动作与成交，三者均满足。该干预同时改变手续费、滑点、点差及原环境内生账户轨迹；不能将改善归因于某一项费用，不能外推至其他行情来源或未来。

新 D 在配置成本下的净收益为 4.83685530%、3.37436660%、4.88461020%；在两种评价成本下的六项原准入探索诊断均满足。但六项都未超过最佳固定 50% 目标的收益，只有种子 42 超过对应现金 E，且三者回撤均未低于对应 E。原市场增益条件未满足；旧 v2 H1/H2 失败结论保持不变。

## 完整性与可复核产物

- 12 项新评价加 20 项冻结 B/C、固定 0/25/50% 与持有参照，共 32 项。每个新模型保存独立 ZIP、原生 JSON actor、模型身份、两种成本的完整成交与权益轨迹。32 项指标、动作、成本、全部判据及模型摘要在测试站只读页 `/gold/ppo/cost-stress` 提供。
- 原生 JSON 的 B/C 是输入模式；D/E 是独立实验身份。每个新模型的 manifest 绑定协议、训练成本、评价成本、actor/ZIP 摘要，不继承原模型的 f64、Linux 或前向证据。
- 预注册协议 SHA256：`3dfc0621c4fb0bda412b42c5d5670b7294fbd395371bef03c9bf25d46c017763`。
- 执行器 SHA256：`7812513e22950e4f0723e9b44cb73f77d4222af9468a0972b10960845821284f`。
- 完整公开摘要 SHA256：`7ddddc0799837d413415df8c0a0dc6c4520e685e28c24e3cd323b904ff3f2175`，48587 字节。本次规范化结果与原始 summary 字节摘要相同。
- 冻结依赖 SHA256：`7849050d054806d56cef63365786028e9b1f140bb6d175dc31fe04ff819ff203`。原 GoldEnv、特征、奖励、超参数、数据与尺度未修改。

本地真实执行命令（以下是本机完整路径及实际输出，不是其他机器的安装步骤）：

```sh
/Users/xxx/Documents/Codex/2026-10-12/vercel-plugin-vercel-openai-curated-remote/outputs/ppo-research-20261022/venv/bin/python -m pytest tests/test_gold_rl.py tests/test_gold_rl_experiments.py tests/test_gold_rl_cost_stress.py -q
# 85 passed in 2.74s
/Users/xxx/Documents/Codex/2026-10-12/vercel-plugin-vercel-openai-curated-remote/outputs/ppo-research-20261022/venv/bin/python -u -m scripts.study_gold_ppo_cost_stress --history /Users/xxx/Documents/Codex/2026-10-12/vercel-plugin-vercel-openai-curated-remote/outputs/gold-history-20261021 --baseline /Users/xxx/Documents/Codex/2026-10-22/fc666-ppo-experiments/outputs/ppo-v2-frozen --protocol docs/research/ppo-cost-stress-protocol.json --output /Users/xxx/Documents/Codex/2026-10-12/vercel-plugin-vercel-openai-curated-remote/outputs/ppo-research-20261022/training-cost-intervention/run-01
# {"cases": 32, "H3_supported": true, "market_increment_demonstrated": false, "promotion_authorized": false, "live_trading": false}
```

完整实际命令、逐模型日志与原始 manifest 保存在任务 `outputs/ppo-research-20261022/training-cost-intervention/`，没有上传模型或原始历史行情到测试站。后续纯标准库审计完成 154 项检查、0 差异：独立重算保存成交的资金、数量、费用、已实现损益与权益回撤，核对全部 20 项控制和全部判据，且单种子费用失败、现金退化两个内存反例均不能获得成功判定。它没有重新训练、推理、载入 pickle 或修改原始产物。

## 同源资料准备与剩余条件

并行只读归档实际保存 15546 条 Swissquote 已存采样报价，gzip 931026 字节；压缩 SHA256 为 `bd72e4b55da8b456737c92aad7a523d49e5b7d65cb539b35cfff8e9f4bcb1002`。在全新的本地 quotes-only SQLite 中恢复后，逐行摘要与数量一致，旧 15440 条前缀摘要也一致，源库 `total_changes=0`。同一快照有 42 个完整小时；9 个不完整小时仍被排除。这是保存与恢复证据，不是完整逐笔 feed、逐行供应商原始响应或已收齐的未来窗口。

尚须冻结真正未来的全部模型对比、验证精确部署策略的数值/时限/成本轨迹，并完成同源覆盖、来源迁移、原三种子双成本收益风险门槛及真实前向模拟执行。100 小时预热和本轮历史诊断都不能替代这些条件。当前模型未注册，POLICY 与实盘继续关闭；旧账户与账本不变。
