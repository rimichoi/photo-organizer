"""날짜 정리용 DB 확장 테스트 (spec §3.3)."""
from photo_organizer.core.database import Database


def _add(db: Database, path: str, exif_dt: float | None = None) -> int:
    db.add_file(path, 4, 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if exif_dt is not None:
        db.set_exif_dates([(exif_dt, fid)])
    db.conn.commit()
    return fid


def test_iter_files_for_organize_excludes_removed_and_missing(tmp_path):
    db = Database(tmp_path / "t.db")
    keep = _add(db, "/photos/keep.jpg", 1_700_000_000.0)
    gone = _add(db, "/photos/gone.jpg")
    trashed = _add(db, "/photos/trashed.jpg")
    db.mark_missing([gone], 1)
    db.mark_removed([trashed], 1)

    rows = list(db.iter_files_for_organize())
    assert [r["id"] for r in rows] == [keep]
    assert rows[0]["path"] == "/photos/keep.jpg"
    assert rows[0]["exif_dt"] == 1_700_000_000.0
    db.close()


def test_update_paths_moves_row(tmp_path):
    db = Database(tmp_path / "t.db")
    fid = _add(db, "/photos/a.jpg")

    conflicts = db.update_paths([("/sorted/2023/2023-04/a.jpg", fid)])

    assert conflicts == 0
    row = db.conn.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()
    assert row["path"] == "/sorted/2023/2023-04/a.jpg"
    db.close()


def test_update_paths_replaces_ghost_row(tmp_path):
    # 목적 경로에 과거 스캔의 유령(missing) 행이 있으면 그 행을 치우고 갱신한다.
    db = Database(tmp_path / "t.db")
    ghost = _add(db, "/sorted/2023/2023-04/a.jpg")
    db.mark_missing([ghost], 1)
    fid = _add(db, "/photos/a.jpg")

    conflicts = db.update_paths([("/sorted/2023/2023-04/a.jpg", fid)])

    assert conflicts == 0
    rows = db.conn.execute("SELECT id FROM files WHERE path=?",
                           ("/sorted/2023/2023-04/a.jpg",)).fetchall()
    assert [r["id"] for r in rows] == [fid]
    assert db.conn.execute("SELECT 1 FROM files WHERE id=?", (ghost,)).fetchone() is None
    db.close()


def test_update_paths_reports_live_conflict(tmp_path):
    # 목적 경로에 살아있는 행이 있으면 갱신을 포기하고 충돌로 집계한다.
    db = Database(tmp_path / "t.db")
    live = _add(db, "/sorted/2023/2023-04/a.jpg")
    fid = _add(db, "/photos/a.jpg")

    conflicts = db.update_paths([("/sorted/2023/2023-04/a.jpg", fid)])

    assert conflicts == 1
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == "/photos/a.jpg"
    assert db.conn.execute("SELECT 1 FROM files WHERE id=?", (live,)).fetchone() is not None
    db.close()


def test_update_paths_empty_is_noop(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.update_paths([]) == 0
    db.close()
