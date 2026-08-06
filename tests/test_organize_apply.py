"""이동 실행 테스트 (spec §3.2, §4). 파일은 tmp_path에만 만든다."""
import os
from datetime import datetime

from photo_organizer.core.database import Database
from photo_organizer.core.organize import apply_organize, plan_organize

TAKEN = datetime(2023, 4, 12, 10, 0, 0).timestamp()


def _make(db: Database, path: str, data: bytes = b"data", exif_dt=TAKEN) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    db.add_file(path, len(data), 0.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    if exif_dt is not None:
        db.set_exif_dates([(exif_dt, fid)])
    db.conn.commit()
    return fid


def test_apply_moves_file_and_updates_db(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    src = str(tmp_path / "src" / "a.jpg")
    fid = _make(db, src)

    moved, skipped, failed = apply_organize(db, plan_organize(db, dest))

    expected = os.path.join(dest, "2023", "2023-04", "a.jpg")
    assert (moved, skipped, failed) == (1, 0, 0)
    assert os.path.exists(expected)
    assert not os.path.exists(src)
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == expected
    log = db.conn.execute("SELECT action, from_path, to_path FROM action_log").fetchone()
    assert log["action"] == "move"
    assert log["from_path"] == src
    assert log["to_path"] == expected
    db.close()


def test_plan_alone_does_not_touch_disk(tmp_path):
    db = Database(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    _make(db, src)

    plan_organize(db, str(tmp_path / "sorted"))

    assert os.path.exists(src)
    assert not os.path.exists(tmp_path / "sorted")
    db.close()


def test_name_collision_gets_suffix(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _make(db, str(tmp_path / "src1" / "a.jpg"), b"one")
    _make(db, str(tmp_path / "src2" / "a.jpg"), b"two")

    moved, _, failed = apply_organize(db, plan_organize(db, dest))

    month = os.path.join(dest, "2023", "2023-04")
    assert (moved, failed) == (2, 0)
    assert sorted(os.listdir(month)) == ["a (1).jpg", "a.jpg"]
    db.close()


def test_rerun_is_idempotent(tmp_path):
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    _make(db, str(tmp_path / "src" / "a.jpg"))
    apply_organize(db, plan_organize(db, dest))

    moved, skipped, failed = apply_organize(db, plan_organize(db, dest))

    assert (moved, skipped, failed) == (0, 1, 0)
    month = os.path.join(dest, "2023", "2023-04")
    assert os.listdir(month) == ["a.jpg"]
    db.close()


def test_missing_source_counts_as_failed(tmp_path):
    # 계획을 세운 뒤 원본이 사라져도 나머지 파일 처리는 계속된다(NFR-03).
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    doomed = str(tmp_path / "src" / "gone.jpg")
    _make(db, doomed)
    _make(db, str(tmp_path / "src" / "ok.jpg"))
    plan = plan_organize(db, dest)
    os.remove(doomed)

    moved, _, failed = apply_organize(db, plan)

    assert (moved, failed) == (1, 1)
    assert os.path.exists(os.path.join(dest, "2023", "2023-04", "ok.jpg"))
    db.close()
