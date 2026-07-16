"""안전 작업 테스트 (SPEC 3.2 데이터 안전성): 휴지통/격리 이동 + 되돌리기."""
import os

from photo_organizer.core import actions
from photo_organizer.core.actions import _unique_dest
from photo_organizer.core.database import Database


def _add(db: Database, path: str, size: int = 4) -> int:
    db.add_file(path, size, 0.0, "jpg")
    db.conn.commit()
    return db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def test_trash_marks_removed_and_logs(tmp_path, monkeypatch):
    # 실제 OS 휴지통 오염 방지: send2trash를 os.remove로 대체
    monkeypatch.setattr(actions, "send2trash", lambda p: os.remove(p))
    f = tmp_path / "a.jpg"
    f.write_bytes(b"data")
    db = Database(tmp_path / "t.db")
    fid = _add(db, str(f))

    ok, failed = actions.trash_files(db, [fid])
    assert (ok, failed) == (1, 0)
    assert not f.exists()
    assert db.count_files() == 0  # removed 제외
    assert db.conn.execute("SELECT removed FROM files WHERE id=?", (fid,)).fetchone()["removed"] == 1
    assert db.conn.execute("SELECT action FROM action_log").fetchone()["action"] == "trash"
    assert db.last_undoable_batch() is None  # 휴지통은 앱 되돌리기 대상 아님
    db.close()


def test_quarantine_and_undo(tmp_path):
    f = tmp_path / "sub" / "b.jpg"
    f.parent.mkdir()
    f.write_bytes(b"data")
    qdir = tmp_path / "q"
    db = Database(tmp_path / "t.db")
    fid = _add(db, str(f))

    ok, failed = actions.quarantine_files(db, [fid], str(qdir))
    assert (ok, failed) == (1, 0)
    assert not f.exists()
    assert list(qdir.iterdir())          # 격리 폴더로 이동됨
    assert db.count_files() == 0

    restored = actions.undo_last(db)
    assert restored == 1
    assert f.exists()                    # 원위치 복구
    assert db.count_files() == 1
    assert db.last_undoable_batch() is None  # 되돌린 배치는 소진
    db.close()


def test_trash_excludes_from_views(tmp_path, monkeypatch):
    """정리된 파일은 중복/유사 등 뷰 쿼리에서 사라진다."""
    monkeypatch.setattr(actions, "send2trash", lambda p: os.remove(p))
    a = tmp_path / "a.jpg"; a.write_bytes(b"same")
    b = tmp_path / "b.jpg"; b.write_bytes(b"same")  # 동일 크기·내용
    db = Database(tmp_path / "t.db")
    fa, fb = _add(db, str(a)), _add(db, str(b))
    db.save_duplicate_groups({"h": [(fa, str(a)), (fb, str(b))]})

    assert len(list(db.iter_duplicate_groups())) == 2
    actions.trash_files(db, [fb])
    # b가 사라져 그룹 조회에 1건만 남는다
    assert {r["file_id"] for r in db.iter_duplicate_groups()} == {fa}
    db.close()


def test_unique_dest_collision(tmp_path):
    (tmp_path / "x.jpg").write_bytes(b"1")
    dest = _unique_dest(str(tmp_path), "x.jpg")
    assert dest.endswith("x (1).jpg")
