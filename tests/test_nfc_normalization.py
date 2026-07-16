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
