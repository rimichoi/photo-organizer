from __future__ import annotations

import os
import unicodedata

from photo_organizer.core.database import Database
from photo_organizer.core.platform_utils import to_nfc
from photo_organizer.core.scanner import scan_directory

# 조합형(NFD) "한글.jpg"
_NFD_NAME = unicodedata.normalize("NFD", "한글.jpg")
_NFC_NAME = unicodedata.normalize("NFC", "한글.jpg")


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_to_nfc_composes_hangul():
    assert to_nfc(_NFD_NAME) == _NFC_NAME
    assert to_nfc(_NFC_NAME) == _NFC_NAME  # 멱등


def test_scan_stores_nfc_path(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / _NFD_NAME))
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    paths = [r["path"] for r in db.conn.execute("SELECT path FROM files")]
    assert len(paths) == 1
    # 저장된 경로의 파일명 성분은 NFC여야 한다.
    assert os.path.basename(paths[0]) == _NFC_NAME
    assert unicodedata.normalize("NFC", paths[0]) == paths[0]


def test_rescan_nfd_does_not_create_duplicate_or_missing(tmp_path):
    root = tmp_path / "photos"
    _write(str(root / _NFD_NAME))
    _write(str(root / "keep.jpg"))  # bystander (안전 가드 회피)
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    # 변화 없이 삭제 감지 재스캔 → 중복 생성/missing 오탐 없어야 함
    s = scan_directory(db, str(root), detect_deletions=True)
    assert db.count_files() == 2
    assert s["deleted"] == 0


def test_migration_renormalizes_existing_nfd_rows(tmp_path):
    dbfile = tmp_path / "lib.db"
    db = Database(dbfile)
    # user_version을 0으로 되돌리고 NFD 경로를 직접 삽입(구 DB 시뮬레이션)
    db.conn.execute("PRAGMA user_version=0")
    nfd_path = "/root/" + _NFD_NAME
    db.conn.execute(
        "INSERT INTO files(path, size, mtime, format) VALUES (?,?,?,?)",
        (nfd_path, 100, 1.0, "jpg"),
    )
    db.conn.commit()
    db.close()
    # 재오픈 → _migrate가 NFC로 재정규화
    db2 = Database(dbfile)
    row = db2.conn.execute("SELECT path FROM files").fetchone()
    assert row["path"] == "/root/" + _NFC_NAME
    assert db2.conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_deletion_detection_works_with_nfd_root_dir(tmp_path):
    """root 디렉토리 이름 자체가 NFD 한글이면(macOS 폴더 선택기) prefix 매칭이
    깨져 삭제 감지가 아무것도 못 잡던 버그(root를 NFC로 정규화해야 함)."""
    import unicodedata

    nfd_dir = unicodedata.normalize("NFD", "사진폴더")
    root = tmp_path / nfd_dir
    _write(str(root / "a.jpg"))
    _write(str(root / "keep.jpg"))  # bystander
    db = Database(tmp_path / "lib.db")
    scan_directory(db, str(root))
    os.remove(str(root / "a.jpg"))
    s = scan_directory(db, str(root), detect_deletions=True)
    assert s["deleted"] == 1  # 수정 전엔 0 (오탐)
    assert db.count_files() == 1


def test_migration_cleans_dependent_rows_on_duplicate(tmp_path):
    """NFC 마이그레이션에서 NFD 중복 행을 삭제할 때 그 행을 참조하는
    duplicate_groups/similar_groups/action_log 행도 함께 정리되어야 한다
    (FK cascade가 없어 고아 참조가 남으면 안 됨)."""
    import unicodedata

    dbfile = tmp_path / "lib.db"
    db = Database(dbfile)
    db.conn.execute("PRAGMA user_version=0")
    nfc_path = "/root/" + unicodedata.normalize("NFC", "한글.jpg")
    nfd_path = "/root/" + unicodedata.normalize("NFD", "한글.jpg")
    # NFC 행(살아남을 것) + NFD 중복 행(삭제될 것)
    db.conn.execute(
        "INSERT INTO files(path,size,mtime,format) VALUES (?,?,?,?)",
        (nfc_path, 100, 1.0, "jpg"),
    )
    cur = db.conn.execute(
        "INSERT INTO files(path,size,mtime,format) VALUES (?,?,?,?)",
        (nfd_path, 100, 1.0, "jpg"),
    )
    nfd_id = cur.lastrowid
    # NFD 행에 의존 행 심기
    db.conn.execute(
        "INSERT INTO duplicate_groups(group_id,file_id,is_representative) VALUES (1,?,0)",
        (nfd_id,),
    )
    db.conn.commit()
    db.close()
    db2 = Database(dbfile)  # 재오픈 → 마이그레이션
    paths = [r["path"] for r in db2.conn.execute("SELECT path FROM files")]
    assert paths == [nfc_path]  # NFC만 생존
    orphans = db2.conn.execute(
        "SELECT COUNT(*) c FROM duplicate_groups WHERE file_id=?", (nfd_id,)
    ).fetchone()["c"]
    assert orphans == 0
