from __future__ import annotations

import os

from photo_organizer.core.database import Database
from photo_organizer.core.scanner import scan_directory


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_scan_summary_new_and_unchanged(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    _write(str(root / "b.jpg"))
    db = Database(tmp_path / "lib.db")
    s1 = scan_directory(db, str(root))
    assert s1["new"] == 2 and s1["updated"] == 0
    s2 = scan_directory(db, str(root))
    assert s2["new"] == 0 and s2["unchanged"] == 2


def test_scan_detects_modification(tmp_path):
    root = tmp_path / "photos"
    f = str(root / "a.jpg")
    _write(f, b"x")
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    _write(f, b"xxxxxx")  # size 변경
    s = scan_directory(db, str(root))
    assert s["updated"] == 1


def test_detect_deletions_marks_missing_scoped(tmp_path):
    root = tmp_path / "photos"
    other = tmp_path / "other"
    _write(str(root / "a.jpg"))
    _write(str(root / "b.jpg"))
    _write(str(other / "c.jpg"))
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    scan_directory(db, str(other))
    assert db.count_files() == 3
    # root 에서 b 삭제 후 삭제 감지 재스캔
    os.remove(str(root / "b.jpg"))
    s = scan_directory(db, str(root), detect_deletions=True)
    assert s["deleted"] == 1
    # b 만 missing, other/c 는 무영향
    assert db.count_files() == 2
    miss = db.conn.execute(
        "SELECT path FROM files WHERE missing=1"
    ).fetchall()
    assert [r["path"] for r in miss] == [str(root / "b.jpg")]


def test_deleted_file_refound_restores(tmp_path):
    root = tmp_path / "photos"
    f = str(root / "a.jpg")
    keep = str(root / "keep.jpg")  # bystander: root 에 최소 1개는 남겨 안전 가드(빈 walk)를 피한다
    _write(f)
    _write(keep)
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    os.remove(f)
    scan_directory(db, str(root), detect_deletions=True)
    assert db.count_files() == 1  # a 만 missing, keep 은 그대로
    _write(f)  # 되살아남
    scan_directory(db, str(root), detect_deletions=True)
    assert db.count_files() == 2
    row = db.conn.execute("SELECT missing FROM files WHERE path=?", (f,)).fetchone()
    assert row["missing"] == 0


def test_second_rescan_of_already_deleted_reports_zero(tmp_path):
    """이미 missing 처리된 파일은 다음 재스캔에서 다시 deleted 로 잡히면 안 된다(유령 카운트)."""
    root = tmp_path / "photos"
    f = str(root / "a.jpg")
    keep = str(root / "keep.jpg")  # bystander: 안전 가드(빈 walk) 회피
    _write(f)
    _write(keep)
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    os.remove(f)
    s1 = scan_directory(db, str(root), detect_deletions=True)
    assert s1["deleted"] == 1
    # 아무 변화 없이 다시 재스캔 → 이미 missing 인 파일을 또 세면 안 된다
    s2 = scan_directory(db, str(root), detect_deletions=True)
    assert s2["deleted"] == 0


def test_safety_guard_empty_walk_skips_deletion(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / "a.jpg"))
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    # root 를 통째로 접근 불가로: 존재하지 않는 경로로 재스캔(빈 walk)
    gone = tmp_path / "photos_gone"
    # DB 경로는 여전히 root 하위지만 walk 대상 root 를 바꿔 빈 결과를 유도할 수는 없으므로
    # 실제 root 내용을 모두 지워 빈 walk 를 만든다.
    os.remove(str(root / "a.jpg"))
    os.rmdir(str(root))
    os.makedirs(str(root))  # 빈 디렉토리 → walk 0개
    s = scan_directory(db, str(root), detect_deletions=True)
    assert s["deleted"] is None  # 가드 발동 → 삭제 보류
    assert db.count_files() == 1  # a 는 여전히 살아있음(missing 처리 안 됨)
