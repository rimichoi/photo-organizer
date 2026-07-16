from __future__ import annotations

from photo_organizer.core.database import Database


def _add(db, path, size=100, mtime=1.0):
    db.add_file(path, size, mtime, "jpg")
    db.conn.commit()
    return db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def test_missing_column_exists_and_defaults_zero(tmp_path):
    db = Database(tmp_path / "lib.db")
    fid = _add(db, "/root/a.jpg")
    row = db.conn.execute("SELECT missing FROM files WHERE id=?", (fid,)).fetchone()
    assert row["missing"] == 0


def test_mark_missing_hides_from_count_and_queries(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/root/a.jpg", size=100)
    b = _add(db, "/root/b.jpg", size=100)  # 같은 크기 → dedup 후보
    assert db.count_files() == 2
    db.mark_missing([a])
    assert db.count_files() == 1
    # dedup 후보에서 제외 → 크기중복 후보가 사라짐(b 혼자 남음)
    sizes = [r["path"] for r in db.iter_size_duplicates()]
    assert "/root/a.jpg" not in sizes
    # analyze 대상에서도 제외
    need = [r["path"] for r in db.iter_files_needing_analysis()]
    assert need == ["/root/b.jpg"]


def test_paths_under_root_scopes_by_prefix(tmp_path):
    db = Database(tmp_path / "lib.db")
    _add(db, "/rootA/x.jpg")
    _add(db, "/rootA/sub/y.jpg")
    _add(db, "/rootB/z.jpg")
    got = {p for _id, p in db.paths_under_root("/rootA")}
    assert got == {"/rootA/x.jpg", "/rootA/sub/y.jpg"}
