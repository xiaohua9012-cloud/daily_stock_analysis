# ChatGPT 盘前盘后数据出口

每日分析结束后，工作流会生成并提交以下稳定文件：

- `reports/latest.json`：盘前、盘后策略的结构化行情底座。
- `reports/latest.md`：方便人工查看的中文摘要。
- `reports/history/YYYY-MM-DD.json`：按交易日保留的结构化历史。
- `reports/history/YYYY-MM-DD.md`：按交易日保留的摘要。
- `reports/audio/postmarket_latest.txt`：盘后可收听版本。
- `reports/audio/premarket_latest.txt`：下一交易日盘前数据底座收听版。

`latest.json` 明确保留 `provider`、`as_of`、`data_quality`、`missing_fields` 和 `fallback_reason`。字段缺失时写入空值并列入 `missing_fields`，不会编造数据。

## 股票池单一配置

股票池只维护一份：`config/stock_pool.json`。

文件分为三个数组：

- `core`：核心跟踪池。
- `watch`：可操作观察池。
- `star`：科创板风向标池，仅用于产业风向判断。

每日 GitHub Actions 会先读取该文件，自动生成运行所需的 `STOCK_LIST`；结构化报告导出脚本也读取同一文件。因此新增、删除或调组股票后，不再需要同步修改 GitHub Actions Variables。

配置要求：

- 股票代码必须是六位数字字符串。
- 每项必须包含 `code` 和 `name`。
- 同一股票不能重复出现在多个池。
- 三个池均需保留数组结构，允许某个池暂时为空。

旧的 `STOCK_LIST` Variable/Secret 只在 `config/stock_pool.json` 缺失时作为兼容降级，不再是主配置入口。

盘前完整策略还需要在北京时间九点十五分前叠加美股、韩国市场和盘前公告。仓库文件提供的是前一交易日 A 股行情与技术指标底座。
