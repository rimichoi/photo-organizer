"""날짜 정리 되돌리기 테스트 (spec §3.4)."""
import os
from datetime import datetime

from photo_organizer.core import actions
from photo_organizer.core.database import Database
from photo_organizer.core.organize import apply_organize, plan_organize

TAKEN = datetime(2023, 4, 12, 10, 0, 0).timestamp()


def _make(db: Database, path: str) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"data")
    db.add_file(path, 4, 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    db.set_exif_dates([(TAKEN, fid)])
    db.conn.commit()
    return fid


def test_undo_restores_file_and_db_path(tmp_path):
    db = Database(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    fid = _make(db, src)
    apply_organize(db, plan_organize(db, str(tmp_path / "sorted")))

    restored = actions.undo_last(db)

    assert restored == 1
    assert os.path.exists(src)
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == src
    assert db.conn.execute("SELECT undone FROM action_log").fetchone()["undone"] == 1
    db.close()


def test_undone_file_stays_visible(tmp_path):
    # move는 removed를 건드리지 않으므로 되돌린 뒤에도 뷰에서 보여야 한다.
    db = Database(tmp_path / "t.db")
    fid = _make(db, str(tmp_path / "src" / "a.jpg"))
    apply_organize(db, plan_organize(db, str(tmp_path / "sorted")))
    actions.undo_last(db)

    row = db.conn.execute("SELECT removed FROM files WHERE id=?", (fid,)).fetchone()
    assert row["removed"] == 0
    db.close()


def test_undo_is_noop_without_batches(tmp_path):
    db = Database(tmp_path / "t.db")
    assert actions.undo_last(db) == 0
    db.close()
