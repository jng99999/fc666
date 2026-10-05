# Phase 6 内置策略执行验收

2026-10-06（Asia/Shanghai）：新增SMA策略在实际研究页面执行，明确fast/slow参数、预热、相等空仓和下一柱成交语义。EMA沿用原语义；新spot-next-open-v3清单记录策略版本和参数。v2旧队列分派保留，离线复现支持v1/v2/v3。详见STRATEGY_REGISTRY.md。

验收结果：
- 120项pytest通过，1条既有Starlette/httpx弃用警告。包含真实PG旧v2队列执行、SMA快照/结果、实际计算中取消及独立Fraction信号参考。
- alembic check无新增迁移；前端类型检查和生产build通过。策略select补充明确可访问名称后重建及浏览器重验通过。
- 已有真实v1/v2导出逐字段复现通过；浏览器下载真实EMA/SMA v3结果逐字段离线复现通过。同一截止历史EMA重跑run_id相同；SMA相同数据SHA而参数/策略生成不同run_id。
- 浏览器research验证实际策略选择、fast3/slow7、刷新恢复SMA结果、无效费率清除旧成功、移动页面宽度及原指标pane交互；无JavaScript异常。
- 浏览器jobs验证停止research进程时排队取消无结果、排队任务经过页面刷新和worker重启后成功、下载复现及完成后刷新恢复；无JavaScript异常。
- API/web/research/market重启后ready。启动脚本和数据库结构没有变化，沿用已保存的四服务云环境启动说明，无需新增安装或配置。

本次未重复终端长时性能测试，未实现自动优化、用户代码执行沙箱、完整事件/向量撮合对照或实盘。实盘仍关闭。下一目标为有界参数批次研究与真实结果比较。
