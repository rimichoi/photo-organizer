from __future__ import annotations

from photo_organizer.core.database import Database


def test_add_file_returns_new_then_unchanged(tmp_path):
    db = Database(tmp_path / "lib.db")
    assert db.add_file("/r/a.jpg", 100, 1.0, "jpg") == "new"
    db.conn.commit()
    assert db.add_file("/r/a.jpg", 100, 1.0, "jpg") == "unchanged"


def test_add_file_size_change_invalidates_derived(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", ("/r/a.jpg",)).fetchone()["id"]
    db.set_analysis_results([("ph", "dh", "/t/1.jpg", "normal", 0.9, fid)])
    db.conn.commit()
    # size 변경 → updated + 파생 NULL
    assert db.add_file("/r/a.jpg", 200, 1.0, "jpg") == "updated"
    db.conn.commit()
    row = db.conn.execute(
        "SELECT size, phash, dhash, thumb_path, category, category_confidence, "
        "scan_status, missing FROM files WHERE id=?", (fid,)
    ).fetchone()
    assert row["size"] == 200
    assert row["phash"] is None and row["dhash"] is None
    assert row["thumb_path"] is None and row["category"] is None
    assert row["category_confidence"] is None
    assert row["scan_status"] == "discovered"


def test_add_file_mtime_change_invalidates(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    db.conn.commit()
    assert db.add_file("/r/a.jpg", 100, 2.0, "jpg") == "updated"


def test_add_file_refind_clears_missing(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", ("/r/a.jpg",)).fetchone()["id"]
    db.mark_missing([fid])
    # 무변경 재발견 → unchanged 이지만 missing 은 0으로 복원
    assert db.add_file("/r/a.jpg", 100, 1.0, "jpg") == "unchanged"
    db.conn.commit()
    row = db.conn.execute("SELECT missing FROM files WHERE id=?", (fid,)).fetchone()
    assert row["missing"] == 0
