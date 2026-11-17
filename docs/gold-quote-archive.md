# 黄金同源采样报价档案

`scripts/archive_gold_quotes.py` 是独立标准库工具，提供 `export / verify / restore`。它不导入业务 `Store`、`create_app`，不启动采样、账户推进、研究恢复或模型，不需要新依赖、SSH 库或凭据。工具也不注册定时任务；部署与持续归档调度仍需单独落实。

## 档案准确含义

这是 **stored sampled quotes（已存储采样报价）**，不是完整逐笔 feed、原始 HTTP 响应或交易所成交数据。源表真实结构仅为 `quotes(ts INTEGER PRIMARY KEY,bid TEXT NOT NULL,ask TEXT NOT NULL)`，由 `core/gold/ledger.py` 持久写入；没有逐条 source、received-at 或供应商响应摘要。header 必须保留这一限制，不能凭档案中的来源字符串声称来源已被独立认证。

当前唯一允许的来源声明是 `swissquote.public:AT:AT:standard:XAU/USD`。header 内来源、部署镜像与 collector 代码证据由操作者提供并审核，工具只验证其结构，不会联系供应商或查询部署管理接口。HistData 历史档案不能改名导入此格式冒充同源采样。

## 导出与校验

从仓库根目录执行，选用已有 Python 3.12 环境：

```sh
python scripts/archive_gold_quotes.py export \
  --database /absolute/path/to/paper.sqlite3 \
  --provenance /absolute/path/to/provenance.json \
  --output /absolute/path/to/new-quotes.jsonl.gz \
  --timeout-seconds 60
```

源库必须已经存在。源连接显式使用 SQLite `mode=ro`、`query_only=ON` 和 `BEGIN` 读快照；只 `SELECT ts,bid,ask FROM quotes ... ORDER BY ts`，没有读取账户/事件或执行迁移。WAL 下查询快照在写出前固定，后续正常 collector 追加不会混入当前档案。此只读连接不是 `immutable=1`，以免错误忽略仍存在的 WAL。SQLite 可能需要使用现有 WAL/SHM 协调读取，故这是逻辑数据只读；不能将“数据无写入”扩张为所有文件访问元数据完全不变。

导出文件以独占方式创建，现有文件、空文件、目录或符号链接均拒绝；导出失败只清理本工具本次新建且身份未被替换的文件。无失败档案被标记成功。`--start-ms N --end-ms N` 是可选的源时间戳半开区间 `[start,end)`；不传则导出当前全部存储采样，空区间结果以 `count=0` 明示，不补造行情。

`provenance.json` 必须恰好包含下面五项，所有 SHA 都必须是实际核对所得的 64 位小写十六进制字符串，示例尖括号不是可执行默认值：

```json
{
  "claimed_source": "swissquote.public:AT:AT:standard:XAU/USD",
  "deployment_image_id": "sha256:<实际完整镜像摘要>",
  "collector_code_sha256": {
    "apps/api/gold_service.py": "<实际运行文件摘要>",
    "core/gold/ledger.py": "<实际运行文件摘要>",
    "core/gold/market.py": "<实际运行文件摘要>"
  },
  "archive_tool_sha256": "<本次实际执行脚本字节摘要>",
  "evidence_sha256": "<另行保留的非敏感部署和collector核验证据文件摘要>"
}
```

不能填测试 SHA、由本地同名文件推断远端 SHA 或把工具填入的声明当成来源证明。证据文件及导出 receipt 应在独立可信位置保存，且不含密钥、Cookie、环境全量或账户账本。

成功导出在 **stderr** 返回单行 JSON receipt，包含 `archive_sha256`（压缩文件全部字节）、`rows_sha256`（升序 `[ts,bid字符串,ask字符串]` 紧凑 JSON + LF）、记录数、首末时间、header 与最后记录摘要、字节计数和 `source_total_changes=0`。只有退出码 0 且完整 receipt 才是成功。

```sh
python scripts/archive_gold_quotes.py verify \
  --archive /absolute/path/to/new-quotes.jsonl.gz \
  --expected-sha256 ACTUAL_SHA256_FROM_TRUSTED_EXPORT_RECEIPT
```

`--expected-sha256` 必填，必须取自可信导出 receipt。只对不可信文件重新算一个 SHA 再作为 expected，不能认证来源或检测整包重新构造。内部 header / 每行链 / footer 检查可以发现损坏；外部压缩 SHA 锚定则拒绝整包改写并重新计算内部链的替换。摘要不含签名，攻击者若能同时改写档案和可信 receipt，超出此工具的认证能力。

## 二进制流与本地恢复

`export --output -` 将 **纯 gzip 字节**写到 stdout，receipt/错误留在 stderr。脚本可通过标准输入执行，不依赖 `__file__`。`--provenance-json '<非敏感JSON>'` 与 `--provenance` 互斥，可在只读远端执行时通过参数提供证据，无需在服务器新增文件。工具不内置 SSH 或账户逻辑，传输必须把 stdout 和 stderr 分开；locale/传输警告不能混进二进制 stdout。

流模式发生超时或非法源数据时，已发送的部分字节不能收回，接收端必须要求成功 receipt、正确退出码并运行 verify。仅有一个 `.gz` 文件或 gzip 魔数不证明导出完成。捕获/固定实际执行脚本的 SHA 与 receipt，再按传输工具规定安全传参；不要把凭据放进 provenance 或命令参数。

```sh
python scripts/archive_gold_quotes.py restore \
  --archive /absolute/path/to/new-quotes.jsonl.gz \
  --expected-sha256 ACTUAL_SHA256_FROM_TRUSTED_EXPORT_RECEIPT \
  --destination /absolute/path/to/brand-new-offline-quotes.sqlite3
```

恢复仅允许**不存在的新路径**，不接受现有空 SQLite、旧库、符号链接或目录；没有 force/覆盖/合并选项。先在目标同目录的全新 0700 私有临时目录中建立 quotes-only 库，全部校验与插入在单个事务内，压缩 SHA、全部记录及 footer 验证完才提交并关闭。随后以 `os.link` 原子发布，若目标在期间被并发创建/替换，发布失败且完整保留并发目标；不提供覆盖 fallback。文件系统不支持 hard-link 则明确失败。失败回滚并仅清理本次私有临时资源。新库不含账户、事件、session、策略或来源伪造字段，不能直接当成业务服务库启动，不得把它交给 `create_app` 推进旧账户。

恢复保留原始存储字符串和源时间戳，不能恢复不存在的 HTTP 内容、收到时间或缺失区间。来源证据保留在档案/receipt 中，而非猜测填入源表没有的列。离线分析若需要 100 根完整小时或 28 天窗口，应另用同一覆盖契约检查这批真实采样，不能把行数、恢复成功或缺口补柱当作数据质量/模型准入。

## 格式、资源与失败边界

- 单 gzip JSONL；首条 header 绑定 schema、限制说明、证据与选择区间；每条 quote 包含升序 index、真实 ts/bid/ask、前序 SHA 和自身 SHA；最后 footer 绑定计数、首末时间、完整报价摘要和 header/最后记录摘要。末尾缺失、多个 footer、footer 后多余记录、重复/乱序时间、非法数值及任意不一致均拒绝。
- 默认最多 1000000 条报价、压缩与解压 JSONL 各最多 256 MiB、单行最多 64 KiB、操作期限 60 秒。`--max-rows / --max-bytes / --timeout-seconds` 可显式调整；不得用无限/非有限值绕过边界。源查询最多读取 `max_rows+1` 条以检测超限；超限是失败，不是悄悄截断。处理流式进行，不把全部报价读入内存。
- SQLite 锁等待最多 min(3 秒, 操作期限)，查询有 progress handler，循环与压缩 IO 周围检查期限。期限是协作式检查，不能中止已经阻塞的系统文件/管道 IO 或 fsync；远端 transport 仍应另设进程总超时，不能把它宣传成硬实时保证。
- 非法、非有限、非正、倒挂 bid/ask、非整数/非递增时间戳以及偏离存储 `str(Decimal)` 序列化的价格字符串均拒绝。历史报价校验不施加“相对当前时间 60 秒新鲜”规则；这里保存历史而非授权成交。
- `verify / restore` 成功摘要写 stdout；任何 CLI 失败退出 2，不注册模型、不返回成交成功。没有输入档案的自动修复、重排序、去重、费用覆盖或盲目重试。

## 本地验证

```sh
python -m pytest tests/test_gold_quote_archive.py -q
```

测试使用真实临时 SQLite、WAL 并发写入、gzip 与 CLI 子进程。覆盖只读 SQL 范围和源库不变、恢复报价与源一致且仅有 quotes 表、已有路径拒绝、真实数据流快照固定、空/半开窗口、坏报价、重复/乱序、链/来源/footer/整包篡改、截断、多尾记录、容量与期限失败、恢复回滚，以及脚本 stdin 执行的纯二进制 stdout。它们是工具工程检查，不能替代线上实际档案、真实恢复或未来前向验证证据。
