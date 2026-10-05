# 长任务交付与发布记录

日期：2026-10-05（Asia/Shanghai）。已完成LONG_TASK_PLAN本轮三个工程切片：指标pane/参数与因果结构、Spot回测账本与清单、真实研究页面/完整导出离线复现。后续范围仍见Phase4/5报告的未完成项。

验收：83项后端测试通过、Alembic无模型差异、TypeScript和production build通过、生产npm审计0漏洞。真实研究浏览器测试及终端断线/恢复/移动端回归通过，无页面JS错误。短时终端回归订单簿received-to-RAF P95 59ms、300samples，frame P95 16.7ms；不是十分钟或生产并发验收。

发布：代码已通过平台现有HTTPS Git代理推送独立分支 codex/fc666-research-platform，初始交付commit 9107d58。main未合并。工作文件、依赖、数据库与原有.env保留；凭据、运行数据/导出、缓存未提交。
云环境启动说明已更新并成功保存draft，返回requires_publish=true；现有工具没有环境发布接口，不将保存草稿说成发布。已运行并验证当前实例启动。GitHub API读请求返回Forbidden，无法据此宣称PR已创建；Git推送已经成功。用户无需重复授权，后续平台接口可用时再执行。

下一切片：完整regime及结构规则扩展、更多撮合/独立向量事件对照、版本化SDK与后台研究任务。实盘保持关闭。
