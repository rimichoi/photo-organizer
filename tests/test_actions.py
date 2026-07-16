"""안전 작업 테스트 (SPEC 3.2 데이터 안전성): 휴지통/격리 이동 + 되돌리기."""
import os

from photo_organizer.core import actions
from photo_organizer.core.actions import _unique_dest
from photo_organizer.core.database import Database
from photo_organizer.core.scanner import scan_directory


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


def test_quarantine_rescan_undo_stays_visible(tmp_path):
    """격리(quarantine) → 삭제 감지 재스캔 → 되돌리기 후에도 파일이 뷰에 다시 보여야 한다.

    회귀 시나리오: paths_under_root 가 removed=1 인 격리 파일까지 대조 대상에
    포함하면, 재스캔이 (이미 격리되어 원경로에서 사라진) 그 파일을 missing=1 로
    잘못 표시한다. 되돌리기(undo_last)는 removed 만 0으로 되돌리고 missing 은
    건드리지 않으므로, 되돌린 뒤에도 missing=1 이 남아 count_files()/뷰에서
    계속 숨겨지는 버그가 있었다.
    """
    root = tmp_path / "photos"
    a = str(root / "a.jpg")
    keep = str(root / "keep.jpg")  # bystander: 안전 가드(빈 walk) 회피
    os.makedirs(root)
    with open(a, "wb") as fh:
        fh.write(b"a")
    with open(keep, "wb") as fh:
        fh.write(b"keep")
    qdir = tmp_path / "q"  # root 밖의 격리 폴더

    db = Database(tmp_path / "t.db")
    scan_directory(db, str(root))
    assert db.count_files() == 2

    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (a,)).fetchone()["id"]
    ok, failed = actions.quarantine_files(db, [fid], str(qdir))
    assert (ok, failed) == (1, 0)
    assert db.count_files() == 1  # a 는 격리되어 뷰에서 숨겨짐

    # 삭제 감지 재스캔: 격리된 a 가 missing 으로 오염되면 안 된다
    scan_directory(db, str(root), detect_deletions=True)

    restored = actions.undo_last(db)
    assert restored == 1

    row = db.conn.execute(
        "SELECT removed, missing FROM files WHERE id=?", (fid,)
    ).fetchone()
    assert (row["removed"], row["missing"]) == (0, 0)
    assert db.count_files() == 2  # 되돌린 a 가 다시 보여야 한다
