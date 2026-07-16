from __future__ import annotations

from photo_organizer.cli import _report_similar
from photo_organizer.core.database import Database


def _add(db, path):
    db.add_file(path, 100, 1.0, "jpg")
    return db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def test_report_similar_console_with_data_does_not_raise(tmp_path, capsys):
    """이전엔 콘솔 출력부가 정의되지 않은 group_ids를 참조해 NameError였다(회귀 방지)."""
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg")
    b = _add(db, "/r/b.jpg")
    db.conn.executemany(
        "INSERT INTO similar_groups(group_id, file_id, similarity_score, is_best_shot) "
        "VALUES (?,?,?,?)",
        [(1, a, 0.9, 1), (1, b, 0.8, 0)],
    )
    db.conn.commit()
    _report_similar(db, None, None)  # 콘솔 경로 — 예외 없이 동작해야 함
    out = capsys.readouterr().out
    assert "유사그룹: 1개 그룹" in out
