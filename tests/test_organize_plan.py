"""이동 계획 생성 테스트 (spec §3.2)."""
import os
from datetime import datetime

from photo_organizer.core.database import Database
from photo_organizer.core.organize import DEFAULT_UNKNOWN_DIR, plan_organize


def _add(db: Database, path: str, exif_dt: float | None = None) -> int:
    db.add_file(path, 4, 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if exif_dt is not None:
        db.set_exif_dates([(exif_dt, fid)])
    db.conn.commit()
    return fid


def _by_src(plan):
    return {os.path.basename(p.src): p for p in plan}


def test_plan_uses_exif_then_filename_then_unknown(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _add(db, "/photos/exif.jpg", datetime(2023, 4, 12, 10, 0, 0).timestamp())
    _add(db, "/photos/IMG_20220105_090000.jpg")
    _add(db, "/photos/DSC_0001.jpg")

    plan = _by_src(plan_organize(db, dest))

    assert plan["exif.jpg"].dest_dir == os.path.join(dest, "2023", "2023-04")
    assert plan["exif.jpg"].source == "exif"
    assert plan["IMG_20220105_090000.jpg"].dest_dir == os.path.join(dest, "2022", "2022-01")
    assert plan["IMG_20220105_090000.jpg"].source == "filename"
    assert plan["DSC_0001.jpg"].dest_dir == os.path.join(dest, DEFAULT_UNKNOWN_DIR)
    assert plan["DSC_0001.jpg"].source == "unknown"
    db.close()


def test_plan_marks_already_sorted_as_skip(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    already = os.path.join(dest, "2023", "2023-04", "a.jpg")
    _add(db, already, datetime(2023, 4, 12, 10, 0, 0).timestamp())

    (item,) = plan_organize(db, dest)

    assert item.skip is True
    db.close()


def test_plan_honors_custom_unknown_dir(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _add(db, "/photos/DSC_0001.jpg")

    (item,) = plan_organize(db, dest, unknown_dir="_nodate")

    assert item.dest_dir == os.path.join(dest, "_nodate")
    db.close()


def test_plan_excludes_removed(tmp_path):
    db = Database(tmp_path / "t.db")
    fid = _add(db, "/photos/a.jpg")
    db.mark_removed([fid], 1)

    assert plan_organize(db, str(tmp_path / "sorted")) == []
    db.close()
