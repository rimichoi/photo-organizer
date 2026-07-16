from __future__ import annotations

import csv

from photo_organizer.cli import build_parser, _cmd_report
from photo_organizer.core.database import Database


def _seed(db):
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", ("/r/a.jpg",)).fetchone()["id"]
    b = db.next_action_batch()
    db.record_actions(b, [(fid, "quarantine", "/r/a.jpg", "/q/a.jpg")])
    return fid


def test_kind_actions_parses():
    args = build_parser().parse_args(["--db", "x.db", "report", "--kind", "actions"])
    assert args.kind == "actions"


def test_iter_action_log_returns_recorded(tmp_path):
    db = Database(tmp_path / "lib.db")
    _seed(db)
    rows = list(db.iter_action_log())
    assert len(rows) == 1
    assert rows[0]["action"] == "quarantine"
    assert rows[0]["to_path"] == "/q/a.jpg"


def test_report_actions_csv(tmp_path):
    db_path = str(tmp_path / "lib.db")
    with Database(db_path) as db:
        _seed(db)
    out = str(tmp_path / "audit.csv")

    class NS:
        db = db_path
        kind = "actions"
        csv = out
        json = None

    rc = _cmd_report(NS())
    assert rc == 0
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["timestamp", "action", "batch", "undone", "from_path", "to_path"]
    # 데이터 1행: action=격리, from/to 경로 포함
    assert rows[1][1] == "격리"
    assert rows[1][4] == "/r/a.jpg"
    assert rows[1][5] == "/q/a.jpg"


def test_report_actions_console_empty(tmp_path, capsys):
    db_path = str(tmp_path / "lib.db")
    Database(db_path).close()

    class NS:
        db = db_path
        kind = "actions"
        csv = None
        json = None

    assert _cmd_report(NS()) == 0
    assert "정리 내역이 없습니다" in capsys.readouterr().out
