#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export stable, Chinese, listening-friendly report artifacts for ChatGPT.

This script is intentionally independent from the application's ORM so it can run
at the end of GitHub Actions even when part of the analysis degraded. It reads the
SQLite database and the generated Markdown report, then writes a stable contract:

- reports/latest.json
- reports/latest.md
- reports/history/YYYY-MM-DD-premarket.json
- reports/history/YYYY-MM-DD-premarket.md
- reports/history/YYYY-MM-DD-postmarket.json
- reports/history/YYYY-MM-DD-postmarket.md
- reports/audio/postmarket_latest.txt
- reports/audio/premarket_latest.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STOCK_POOL_PATH = Path("config/stock_pool.json")
POOL_KEYS = {
    "core": "核心池",
    "watch": "观察池",
    "star": "科创板风向标",
}

COMPANY_TRANSLATIONS = {
    r"\bNVIDIA\b|\bNvidia\b": "英伟达",
    r"\bMicron(?: Technology)?\b": "美光科技",
    r"\bVertiv\b": "维谛技术",
    r"\bBroadcom\b": "博通",
    r"\bMarvell(?: Technology)?\b": "迈威尔科技",
    r"\bASML\b": "阿斯麦",
    r"\bKLA(?: Corporation)?\b": "科磊",
    r"\bApplied Materials\b": "应用材料",
    r"\bLam Research\b": "泛林集团",
    r"\bCoherent\b": "高意",
    r"\bLumentum\b": "朗美通",
    r"\bSamsung Electronics\b": "三星电子",
    r"\bSK Hynix\b": "SK海力士",
    r"\bHanmi Semiconductor\b": "韩美半导体",
}
TERM_TRANSLATIONS = {
    r"\bAI\b": "人工智能", r"\bPCB\b": "印制电路板", r"\bCCL\b": "覆铜板",
    r"\bHBM\b": "高带宽内存", r"\bEDA\b": "电子设计自动化",
    r"\bMA5\b": "五日均线", r"\bMA10\b": "十日均线", r"\bMA20\b": "二十日均线",
    r"\bSOX\b": "费城半导体指数", r"\bNASDAQ\b|\bNasdaq\b": "纳斯达克指数",
}

@dataclass
class ExportContext:
    report_date: str
    generated_at: str
    database_path: Path
    reports_dir: Path
    stock_pool_path: Path = DEFAULT_STOCK_POOL_PATH


def load_stock_pool(path: Path) -> dict[str, dict[str, str]]:
    """Load and validate the single-source stock pool configuration."""
    if not path.exists():
        raise FileNotFoundError(f"股票池配置文件不存在: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"股票池配置不是有效 JSON: {path}: {exc}") from exc

    pools: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for key, label in POOL_KEYS.items():
        entries = raw.get(key)
        if not isinstance(entries, list):
            raise ValueError(f"股票池配置字段 {key} 必须是数组")
        pool: dict[str, str] = {}
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"{key} 第 {index} 项必须是对象")
            code = str(entry.get("code", "")).strip()
            name = str(entry.get("name", "")).strip()
            if not re.fullmatch(r"\d{6}", code):
                raise ValueError(f"{key} 第 {index} 项股票代码必须是六位数字: {code!r}")
            if not name:
                raise ValueError(f"{key} 第 {index} 项缺少股票名称")
            if code in seen:
                raise ValueError(f"股票代码重复出现在多个股票池: {code}")
            seen.add(code)
            pool[code] = name
        pools[label] = pool
    if not seen:
        raise ValueError("股票池配置不能为空")
    return pools


def stock_list_from_config(path: Path) -> str:
    pools = load_stock_pool(path)
    return ",".join(code for pool in pools.values() for code in pool)


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _latest_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    columns = _table_columns(conn, "stock_daily")
    if not columns:
        return {}
    wanted = [
        "code", "date", "close", "pct_chg", "amount", "ma5", "ma10", "ma20",
        "volume_ratio", "data_source",
    ]
    selected = [c for c in wanted if c in columns]
    sql = f"""
        SELECT {', '.join('s.' + c for c in selected)}
        FROM stock_daily s
        JOIN (
            SELECT code, MAX(date) AS max_date FROM stock_daily GROUP BY code
        ) latest ON latest.code = s.code AND latest.max_date = s.date
    """
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(sql):
        item = dict(zip(selected, row))
        result[str(item.get("code", "")).zfill(6)] = item
    return result


def _latest_analysis(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    columns = _table_columns(conn, "analysis_history")
    if not columns:
        return {}
    selected = [c for c in [
        "code", "name", "sentiment_score", "operation_advice", "trend_prediction",
        "analysis_summary", "raw_result", "created_at",
    ] if c in columns]
    sql = f"""
        SELECT {', '.join('a.' + c for c in selected)}
        FROM analysis_history a
        JOIN (
            SELECT code, MAX(id) AS max_id FROM analysis_history GROUP BY code
        ) latest ON latest.max_id = a.id
    """
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(sql):
        item = dict(zip(selected, row))
        code = str(item.get("code", "")).zfill(6)
        raw = item.get("raw_result")
        if isinstance(raw, str):
            try:
                item["raw_result"] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        result[code] = item
    return result


def _extract_turnover(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    keys = {"turnover_rate", "turnover", "换手率"}
    stack: list[Any] = [raw]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).lower() in keys:
                    return _number(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return None


def _trend_from_ma(close: float | None, ma5: float | None, ma10: float | None, ma20: float | None) -> str:
    if None in (close, ma5, ma10, ma20):
        return "数据不足"
    if close > ma5 > ma10 > ma20:
        return "多头排列"
    if close < ma5 < ma10 < ma20:
        return "空头排列"
    if close >= ma20:
        return "二十日均线上方震荡"
    return "二十日均线下方震荡"


def _read_latest_markdown(reports_dir: Path) -> tuple[str, Path | None]:
    candidates = sorted(
        [p for p in reports_dir.glob("*.md") if p.name not in {"latest.md"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return "", None
    try:
        return candidates[0].read_text(encoding="utf-8"), candidates[0]
    except OSError:
        return "", candidates[0]


def _translate(text: str) -> str:
    for pattern, replacement in COMPANY_TRANSLATIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    for pattern, replacement in TERM_TRANSLATIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _digits_for_speech(match: re.Match[str]) -> str:
    zh = {"0": "零", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}
    return "".join(zh[ch] for ch in match.group(0))


def to_listening_text(markdown: str) -> str:
    text = _translate(markdown or "")
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>|]+", " ", text)
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", "，", text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)%", lambda m: ("下降百分之" if m.group(1).startswith("-") else "上涨百分之") + m.group(1).lstrip("-").replace(".", "点"), text)
    text = re.sub(r"(?<!\d)\d{6}(?!\d)", _digits_for_speech, text)
    text = re.sub(r"\b[A-Z]{2,6}\b", lambda m: " ".join(m.group(0)), text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" ，。")
        if not line or re.fullmatch(r"[-=—_\s]+", line):
            continue
        if not re.search(r"[。！？]$", line):
            line += "。"
        lines.append(line)
    return "\n".join(lines)


def _build_json(ctx: ExportContext) -> dict[str, Any]:
    missing_global: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    analyses: dict[str, dict[str, Any]] = {}
    db_ok = ctx.database_path.exists()
    if db_ok:
        try:
            with sqlite3.connect(ctx.database_path) as conn:
                rows = _latest_rows(conn)
                analyses = _latest_analysis(conn)
        except sqlite3.Error:
            missing_global.append("database_read_error")
    else:
        missing_global.append("database")

    pools = load_stock_pool(ctx.stock_pool_path)
    stock_entries = [
        (code, name, pool_label)
        for pool_label, pool in pools.items()
        for code, name in pool.items()
    ]

    stocks = []
    for code, configured_name, pool_label in stock_entries:
        row = rows.get(code, {})
        analysis = analyses.get(code, {})
        close = _number(row.get("close"))
        ma5, ma10, ma20 = (_number(row.get("ma5")), _number(row.get("ma10")), _number(row.get("ma20")))
        item_missing = []
        values = {
            "close": close,
            "change_pct": _number(row.get("pct_chg")),
            "amount": _number(row.get("amount")),
            "turnover_rate": _extract_turnover(analysis.get("raw_result")),
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "volume_ratio": _number(row.get("volume_ratio")),
        }
        for key, value in values.items():
            if value is None:
                item_missing.append(key)
        provider = row.get("data_source") or "daily_stock_analysis"
        as_of = str(row.get("date") or analysis.get("created_at") or ctx.report_date)
        stock = {
            "code": code,
            "name": analysis.get("name") or configured_name,
            **values,
            "trend": analysis.get("trend_prediction") or _trend_from_ma(close, ma5, ma10, ma20),
            "operation_advice": analysis.get("operation_advice"),
            "analysis_summary": _translate(str(analysis.get("analysis_summary") or "")),
            "provider": provider,
            "as_of": as_of,
            "data_quality": "A" if not item_missing else ("B" if len(item_missing) <= 2 else "C"),
            "missing_fields": item_missing,
            "pool": pool_label,
        }
        stocks.append(stock)

    available = sum(1 for stock in stocks if stock["close"] is not None)
    quality = "A" if available == len(stocks) and not missing_global else ("B" if available >= len(stocks) * 0.7 else "C")
    if available < len(stocks):
        missing_global.append("partial_stock_quotes")
    return {
        "schema_version": "a_share_v4.1",
        "report_date": ctx.report_date,
        "generated_at": ctx.generated_at,
        "provider": "daily_stock_analysis",
        "as_of": max((s["as_of"] for s in stocks if s.get("as_of")), default=ctx.report_date),
        "data_quality": quality,
        "missing_fields": sorted(set(missing_global)),
        "fallback_reason": "部分字段由数据库现有字段降级生成" if missing_global else None,
        "market_summary": {
            "stock_count": len(stocks), "available_quote_count": available,
            "core_pool_count": len(pools["核心池"]),
            "watch_pool_count": len(pools["观察池"]),
            "star_pool_count": len(pools["科创板风向标"]),
        },
        "stocks": stocks,
    }


def _summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# A股结构化行情底座（{payload['report_date']}）",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 数据来源：{payload['provider']}",
        f"- 数据质量：{payload['data_quality']}",
        f"- 可用行情：{payload['market_summary']['available_quote_count']} / {payload['market_summary']['stock_count']}",
        "",
        "## 股票数据",
        "",
        "| 股票 | 收盘价 | 涨跌幅 | 五日线 | 十日线 | 二十日线 | 数据质量 |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for s in payload["stocks"]:
        def fmt(v: Any, pct: bool = False) -> str:
            if v is None:
                return "[数据待确认]"
            return f"{v:.2f}%" if pct else f"{v:.2f}"
        lines.append(f"| {s['code']} {s['name']} | {fmt(s['close'])} | {fmt(s['change_pct'], True)} | {fmt(s['ma5'])} | {fmt(s['ma10'])} | {fmt(s['ma20'])} | {s['data_quality']} |")
    lines.extend(["", "> 本文件用于盘前与盘后报告的数据底座，不构成投资建议。", ""])
    return "\n".join(lines)


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据待确认"
    return f"{value:.{digits}f}"


def _pool_stocks(payload: dict[str, Any], pool: str) -> list[dict[str, Any]]:
    return [s for s in payload["stocks"] if s["pool"] == pool]


def _rank_stocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按数据完整度、绝对涨跌幅和量比生成收听顺序，不等同于买卖建议。"""
    stocks = [s for s in payload["stocks"] if s["pool"] != "科创板风向标"]
    return sorted(
        stocks,
        key=lambda s: (
            s["close"] is not None,
            abs(s["change_pct"] or 0),
            s["volume_ratio"] or 0,
        ),
        reverse=True,
    )


def _premarket_audio(payload: dict[str, Any]) -> str:
    core = _pool_stocks(payload, "核心池")
    ranked = _rank_stocks(payload)[:3]
    complete = sum(1 for s in payload["stocks"] if s["close"] is not None)

    lines = [
        f"这里是{payload['report_date']}A股盘前跑步收听版。",
        "第一部分，数据质量。",
        f"本次数据质量评级为{payload['data_quality']}。股票池共{payload['market_summary']['stock_count']}只，"
        f"取得有效行情{complete}只。",
    ]

    if payload.get("missing_fields"):
        lines.append("降级字段包括：" + "、".join(payload["missing_fields"]) + "。")
    else:
        lines.append("全局结构化字段没有缺失。")

    lines.extend([
        "第二部分，昨日盘后信号承接。",
        "结构化底座只负责行情和技术指标。昨日盘后结论、海外市场、韩国市场和盘前公告，"
        "需要在九点十五分报告生成时联网补充。",
        "第三部分，今日风险偏好观察。",
        "开盘前先看海外半导体风险偏好是否一致，再看韩国半导体开盘是否承接，"
        "最后看A股核心股是否出现公告增量。",
        "第四部分，今日作战排序。",
    ])

    for idx, stock in enumerate(ranked, start=1):
        direction = "上涨" if (stock["change_pct"] or 0) >= 0 else "下跌"
        lines.append(
            f"第{idx}位，{stock['name']}。昨日收盘{_fmt_num(stock['close'])}元，"
            f"{direction}百分之{_fmt_num(abs(stock['change_pct'] or 0))}，"
            f"技术状态为{stock['trend']}。"
        )

    lines.extend([
        "第五部分，核心股盯盘条件。",
    ])
    for stock in core:
        lines.append(
            f"{stock['name']}，五日均线{_fmt_num(stock['ma5'])}，"
            f"十日均线{_fmt_num(stock['ma10'])}，二十日均线{_fmt_num(stock['ma20'])}。"
            "开盘重点观察是否站稳关键均线，以及放量时价格能否同步走强。"
        )

    lines.extend([
        "第六部分，开盘三十分钟行动清单。",
        "第一，九点十五分到九点二十五分只观察竞价强弱，不预设涨跌。",
        "第二，高开低走时，优先检查核心股是否跌破开盘价并放量。",
        "第三，低开高走时，优先检查是否有板块内两只以上个股同步确认。",
        "第四，海外强不等于A股可以追高，必须验证实际传导。",
        "最后提醒：这份文件是盘前结构化底座，不替代联网新闻和公告核验。",
        "以上仅供研究复盘，不构成投资建议。",
    ])
    return to_listening_text("\n".join(lines))


def _postmarket_audio(payload: dict[str, Any], source_markdown: str) -> str:
    ranked = _rank_stocks(payload)
    strongest = sorted(
        [s for s in ranked if s["change_pct"] is not None],
        key=lambda s: s["change_pct"],
        reverse=True,
    )[:3]
    weakest = sorted(
        [s for s in ranked if s["change_pct"] is not None],
        key=lambda s: s["change_pct"],
    )[:3]

    lines = [
        f"这里是{payload['report_date']}A股盘后跑步收听版。",
        "第一部分，今日市场结论。",
        f"本次结构化数据质量评级为{payload['data_quality']}，"
        f"有效行情{payload['market_summary']['available_quote_count']}只。",
        "第二部分，今日强势股票。",
    ]

    for stock in strongest:
        lines.append(
            f"{stock['name']}，收盘{_fmt_num(stock['close'])}元，"
            f"上涨百分之{_fmt_num(stock['change_pct'])}，技术状态为{stock['trend']}。"
        )

    lines.append("第三部分，今日弱势和风险股票。")
    for stock in weakest:
        direction = "上涨" if (stock["change_pct"] or 0) >= 0 else "下跌"
        lines.append(
            f"{stock['name']}，收盘{_fmt_num(stock['close'])}元，"
            f"{direction}百分之{_fmt_num(abs(stock['change_pct'] or 0))}。"
        )

    lines.extend([
        "第四部分，盘前判断验证。",
        "请把今日盘前的三项验证问题，与收盘后的主线强弱、量能和核心股表现逐项对照。",
        "第五部分，核心股和观察池。",
    ])

    for stock in ranked[:7]:
        lines.append(
            f"{stock['name']}，五日均线{_fmt_num(stock['ma5'])}，"
            f"十日均线{_fmt_num(stock['ma10'])}，二十日均线{_fmt_num(stock['ma20'])}，"
            f"当前判断为{stock['trend']}。"
        )

    lines.extend([
        "第六部分，明日闭环。",
        "明日需要验证三个问题：最强主线是否继续扩散，最弱风险是否继续释放，"
        "以及海外映射能否转化为A股实际承接。",
        "明日最需要防止的误判，是把单只股票冲高误认为整个主线转强。",
    ])

    if source_markdown.strip():
        lines.extend([
            "第七部分，原始盘后报告摘要。",
            to_listening_text(source_markdown),
        ])

    lines.append("以上仅供研究复盘，不构成投资建议。")
    return to_listening_text("\n".join(lines))


def export(ctx: ExportContext, report_mode: str = "postmarket") -> list[Path]:
    if report_mode not in {"premarket", "postmarket"}:
        raise ValueError(f"不支持的报告模式: {report_mode}")

    ctx.reports_dir.mkdir(parents=True, exist_ok=True)
    history_dir = ctx.reports_dir / "history"
    audio_dir = ctx.reports_dir / "audio"
    history_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    payload = _build_json(ctx)
    payload["report_mode"] = report_mode
    markdown, source_report = _read_latest_markdown(ctx.reports_dir)
    latest_md = _summary_markdown(payload)

    mode_audio = (
        _premarket_audio(payload)
        if report_mode == "premarket"
        else _postmarket_audio(payload, markdown)
    )

    outputs = {
        ctx.reports_dir / "latest.json": json.dumps(payload, ensure_ascii=False, indent=2),
        ctx.reports_dir / "latest.md": latest_md,
        history_dir / f"{ctx.report_date}-{report_mode}.json": json.dumps(payload, ensure_ascii=False, indent=2),
        history_dir / f"{ctx.report_date}-{report_mode}.md": latest_md,
        audio_dir / f"{report_mode}_latest.txt": mode_audio,
    }

    # 保留另一时段的 latest 文件，不覆盖、不删除。
    for path, content in outputs.items():
        path.write_text(content.rstrip() + "\n", encoding="utf-8")

    if source_report:
        payload["source_report"] = source_report.name
    return list(outputs)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "./data/stock_analysis.db"))
    parser.add_argument("--reports-dir", default="./reports")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--stock-pool", default=str(DEFAULT_STOCK_POOL_PATH))
    parser.add_argument("--print-stock-list", action="store_true")
    parser.add_argument("--report-mode", choices=("premarket", "postmarket"), default="postmarket")
    args = parser.parse_args()
    stock_pool_path = Path(args.stock_pool)
    if args.print_stock_list:
        print(stock_list_from_config(stock_pool_path))
        return 0
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    ctx = ExportContext(args.date, now, Path(args.database), Path(args.reports_dir), stock_pool_path)
    outputs = export(ctx, args.report_mode)
    print("已生成面向 ChatGPT 的结构化与收听版文件：")
    for path in outputs:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
