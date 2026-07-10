# ChatGPT 盘前盘后数据出口

每日分析结束后，工作流会生成并提交以下稳定文件：

- `reports/latest.json`：盘前、盘后策略的结构化行情底座。
- `reports/latest.md`：方便人工查看的中文摘要。
- `reports/history/YYYY-MM-DD.json`：按交易日保留的结构化历史。
- `reports/history/YYYY-MM-DD.md`：按交易日保留的摘要。
- `reports/audio/postmarket_latest.txt`：盘后可收听版本。
- `reports/audio/premarket_latest.txt`：下一交易日盘前数据底座收听版。

`latest.json` 明确保留 `provider`、`as_of`、`data_quality`、`missing_fields` 和 `fallback_reason`。字段缺失时写入空值并列入 `missing_fields`，不会编造数据。

盘前完整策略还需要在北京时间九点十五分前叠加美股、韩国市场和盘前公告。仓库文件提供的是前一交易日 A 股行情与技术指标底座。
