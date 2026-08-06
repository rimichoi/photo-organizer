"""날짜 정리 견고성 테스트 — 교차 코드 리뷰(2026-08-06)에서 나온 결함 회귀 방지.

다루는 결함:
1. 중단(KeyboardInterrupt) 시 action_log가 비어 되돌리기·재개 불가
2. unique_dest의 존재 검사가 긴 경로 정규화를 거치지 않아 덮어쓰기 위험
3. undo가 원위치에 새로 생긴 파일을 덮어씀
4. removed=1 행이 목적 경로를 점유하면 이동은 됐는데 '실패'로 집계되고 DB stale
5. 앞 항목이 만든 목적 파일을 뒤 항목이 자기 src로 착각해 다시 옮김
"""
import os
from datetime import datetime

import pytest

from photo_organizer.core import actions, organize
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


# --- 1. 중단 안전성 -------------------------------------------------------

def test_interrupt_keeps_log_and_allows_resume(tmp_path, monkeypatch):
    """Ctrl-C로 중단돼도 이미 옮긴 파일은 기록돼야 재개·되돌리기가 가능하다."""
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    ids = [_make(db, str(tmp_path / "src" / f"p{i}.jpg")) for i in range(4)]

    real_move = organize.shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        if calls["n"] == 2:
            raise KeyboardInterrupt
        calls["n"] += 1
        return real_move(src, dst)

    monkeypatch.setattr(organize.shutil, "move", flaky_move)
    with pytest.raises(KeyboardInterrupt):
        apply_organize(db, plan_organize(db, dest))
    monkeypatch.undo()

    # 중단 전에 옮긴 2장이 기록되고 DB 경로도 갱신돼 있어야 한다.
    logged = db.conn.execute(
        "SELECT file_id, to_path FROM action_log WHERE action='move'"
    ).fetchall()
    assert len(logged) == 2
    for row in logged:
        assert os.path.exists(row["to_path"])
        assert db.conn.execute(
            "SELECT path FROM files WHERE id=?", (row["file_id"],)
        ).fetchone()["path"] == row["to_path"]

    # 재실행이 곧 재개: 옮긴 2장은 skip, 남은 2장만 이동.
    moved, skipped, failed, stale = apply_organize(db, plan_organize(db, dest))
    assert (moved, skipped, failed, stale) == (2, 2, 0, 0)
    assert len(os.listdir(os.path.join(dest, "2023", "2023-04"))) == 4
    assert len(ids) == 4
    db.close()


def test_interrupted_batch_is_undoable(tmp_path, monkeypatch):
    """중단된 작업도 되돌리기로 원위치로 되돌릴 수 있어야 한다."""
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    src0 = str(tmp_path / "src" / "p0.jpg")
    _make(db, src0)
    _make(db, str(tmp_path / "src" / "p1.jpg"))

    real_move = organize.shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        if calls["n"] == 1:
            raise KeyboardInterrupt
        calls["n"] += 1
        return real_move(src, dst)

    monkeypatch.setattr(organize.shutil, "move", flaky_move)
    with pytest.raises(KeyboardInterrupt):
        apply_organize(db, plan_organize(db, dest))
    monkeypatch.undo()

    assert actions.undo_last(db) == 1
    assert os.path.exists(src0)
    db.close()


# --- 2. 긴 경로 정규화 ----------------------------------------------------

def test_unique_dest_checks_existence_through_normalizer(tmp_path, monkeypatch):
    """존재 검사도 normalize_long_path를 거쳐야 긴 경로에서 덮어쓰지 않는다."""
    seen: list[str] = []

    def spy(path: str) -> str:
        seen.append(str(path))
        return str(path)

    monkeypatch.setattr(actions, "normalize_long_path", spy)
    (tmp_path / "a.jpg").write_bytes(b"x")

    dest = actions.unique_dest(str(tmp_path), "a.jpg")

    assert dest == os.path.join(str(tmp_path), "a (1).jpg")
    assert any("a.jpg" in p for p in seen), "존재 검사가 정규화를 거치지 않았다"


# --- 3. undo 덮어쓰기 방지 ------------------------------------------------

def test_undo_does_not_overwrite_new_file(tmp_path):
    """되돌릴 때 원위치에 다른 파일이 생겼으면 덮어쓰지 않는다."""
    db = Database(tmp_path / "t.db")
    src = str(tmp_path / "src" / "a.jpg")
    fid = _make(db, src, b"OLD")
    apply_organize(db, plan_organize(db, str(tmp_path / "sorted")))
    with open(src, "wb") as fh:  # 사용자가 원위치에 새 사진을 넣음
        fh.write(b"BRAND-NEW")

    assert actions.undo_last(db) == 1

    assert open(src, "rb").read() == b"BRAND-NEW", "새 파일이 덮어써졌다"
    restored = db.conn.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()["path"]
    assert restored == os.path.join(str(tmp_path / "src"), "a (1).jpg")
    assert open(restored, "rb").read() == b"OLD"
    db.close()


# --- 4. removed 행이 목적 경로를 점유 -------------------------------------

def test_removed_row_does_not_block_path_update(tmp_path):
    """격리된(removed=1) 유령 행이 목적 경로를 쥐고 있어도 갱신돼야 한다."""
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "sorted")
    occupied = os.path.join(dest, "2023", "2023-04", "a.jpg")
    ghost = _make(db, occupied, b"gone")
    db.mark_removed([ghost], 1)
    os.remove(occupied)
    fid = _make(db, str(tmp_path / "src" / "a.jpg"), b"real")

    moved, skipped, failed, stale = apply_organize(db, plan_organize(db, dest))

    assert (moved, skipped, failed, stale) == (1, 0, 0, 0)
    assert db.conn.execute("SELECT path FROM files WHERE id=?",
                           (fid,)).fetchone()["path"] == occupied
    assert db.conn.execute("SELECT 1 FROM files WHERE id=?", (ghost,)).fetchone() is None
    db.close()


# --- 5. 낡은 계획 항목 -----------------------------------------------------

def test_stale_plan_entry_does_not_move_new_file(tmp_path):
    """이번 실행이 방금 만든 목적 파일을 뒤 항목이 자기 src로 착각하면 안 된다."""
    db = Database(tmp_path / "t.db")
    dest = str(tmp_path / "lib")
    # id=1: 새 사진 → 2023/2023-04 로 이동 예정
    _make(db, str(tmp_path / "lib" / "new" / "a.jpg"), b"REAL")
    # id=2: 이미 지워졌지만 DB에 남은 유령 행. 경로가 id=1의 목적지와 겹친다.
    ghost_path = os.path.join(dest, "2023", "2023-04", "a.jpg")
    _make(db, ghost_path, b"ghost", exif_dt=None)
    os.remove(ghost_path)

    moved, skipped, failed, stale = apply_organize(db, plan_organize(db, dest))

    assert os.path.exists(ghost_path), "이동한 파일이 그대로 있어야 한다"
    assert open(ghost_path, "rb").read() == b"REAL"
    assert not os.path.exists(os.path.join(dest, "_날짜미상", "a.jpg")), \
        "방금 만든 파일이 다시 옮겨졌다"
    assert (moved, skipped, failed) == (1, 0, 1)  # 유령 항목은 실패로 집계
    # 유령 행이 목적 경로를 쥐고 있어 DB 경로 갱신은 미뤄진다(재스캔이 정리).
    assert stale == 1
    db.close()
