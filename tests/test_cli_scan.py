from __future__ import annotations

import os

from photo_organizer.cli import build_parser, _cmd_scan


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_scan_flag_parses_detect_deletions():
    parser = build_parser()
    args = parser.parse_args(["--db", "x.db", "scan", "/p", "--detect-deletions"])
    assert args.detect_deletions is True


def test_cmd_scan_reports_summary(tmp_path, capsys):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    db_path = str(tmp_path / "lib.db")

    class NS:
        db = db_path
        path = str(root)
        detect_deletions = False

    rc = _cmd_scan(NS())
    assert rc == 0
    out = capsys.readouterr().out
    assert "신규" in out
