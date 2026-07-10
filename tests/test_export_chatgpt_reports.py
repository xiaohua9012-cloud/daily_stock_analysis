import json
import sqlite3
from pathlib import Path

from scripts.export_chatgpt_reports import ExportContext, export, to_listening_text


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE stock_daily (
          id INTEGER PRIMARY KEY, code TEXT, date TEXT, close REAL, amount REAL,
          pct_chg REAL, ma5 REAL, ma10 REAL, ma20 REAL, volume_ratio REAL,
          data_source TEXT
        );
        CREATE TABLE analysis_history (
          id INTEGER PRIMARY KEY, code TEXT, name TEXT, sentiment_score INTEGER,
          operation_advice TEXT, trend_prediction TEXT, analysis_summary TEXT,
          raw_result TEXT, created_at TEXT
        );
        """)
        conn.execute(
            "INSERT INTO stock_daily VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            ("000988", "2026-07-09", 55.2, 123456.0, 3.2, 54.0, 52.0, 50.0, 1.3, "AkShare"),
        )
        conn.execute(
            "INSERT INTO analysis_history VALUES (1,?,?,?,?,?,?,?,?)",
            ("000988", "华工科技", 80, "观察", "偏强", "AI 光通信修复", '{"turnover_rate": 2.5}', "2026-07-09 18:00:00"),
        )


def test_export_contract_and_audio(tmp_path: Path):
    db = tmp_path / "stock.db"
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report_20260709.md").write_text("# NVIDIA 与 AI\n000988 华工科技上涨3.2%", encoding="utf-8")
    _make_db(db)

    export(ExportContext("2026-07-09", "2026-07-09T18:30:00+08:00", db, reports))

    payload = json.loads((reports / "latest.json").read_text(encoding="utf-8"))
    stock = next(s for s in payload["stocks"] if s["code"] == "000988")
    assert stock["name"] == "华工科技"
    assert stock["close"] == 55.2
    assert stock["turnover_rate"] == 2.5
    assert payload["provider"] == "daily_stock_analysis"
    audio = (reports / "audio/postmarket_latest.txt").read_text(encoding="utf-8")
    assert "英伟达" in audio
    assert "人工智能" in audio
    assert "零零零九八八" in audio
    assert "百分之" in audio


def test_listening_text_removes_markdown():
    text = to_listening_text("## 标题\n| NVDA | AI | MA5 |")
    assert "#" not in text
    assert "|" not in text
    assert "人工智能" in text
    assert "五日均线" in text
