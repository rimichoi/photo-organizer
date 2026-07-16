from __future__ import annotations

from photo_organizer.core.config import Config
from photo_organizer.core.database import Database
from photo_organizer.core.image_loader import parse_exif_datetime
from photo_organizer.classify.similar import cluster_similar


def test_parse_exif_datetime():
    assert parse_exif_datetime("2026:07:16 12:00:00") is not None
    assert parse_exif_datetime(None) is None
    assert parse_exif_datetime("garbage") is None


def _add(db, path, phash, exif_dt=None):
    db.add_file(path, 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    db.conn.execute("UPDATE files SET phash=?, exif_dt=?, scan_status='analyzed' WHERE id=?",
                    (phash, exif_dt, fid))
    db.conn.commit()
    return fid


# pHash 거리가 strict(5)보다 크지만 burst(10) 이내인 두 해시
_H1 = "0000000000000000"
_H2 = "00000000000000ff"  # _H1과 해밍거리 8 (5 초과, 10 이내)
_H_FAR = "ffffffffffffffff"  # 거리 64 (버스트도 초과)


def test_burst_groups_close_in_time(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg", _H1, exif_dt=1000.0)
    b = _add(db, "/r/b.jpg", _H2, exif_dt=1001.0)  # 1초 차, 버스트 거리 이내
    groups = cluster_similar(db, cfg=Config())
    ids = {fid for g in groups for fid, _ in g}
    assert a in ids and b in ids
    # 같은 그룹인지: 한 그룹에 a,b 둘 다
    assert any({a, b} <= {fid for fid, _ in g} for g in groups)


def test_burst_not_grouped_when_far_in_time(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg", _H1, exif_dt=1000.0)
    b = _add(db, "/r/b.jpg", _H2, exif_dt=9999.0)  # 시간 멀어 버스트 아님
    groups = cluster_similar(db, cfg=Config())
    # strict(거리8 > 5)도 아니고 버스트(시간 멀다)도 아니므로 그룹 없음
    assert not any({a, b} <= {fid for fid, _ in g} for g in groups)


def test_burst_not_grouped_when_phash_too_far(tmp_path):
    db = Database(tmp_path / "lib.db")
    a = _add(db, "/r/a.jpg", _H1, exif_dt=1000.0)
    b = _add(db, "/r/b.jpg", _H_FAR, exif_dt=1001.0)  # 시간 가깝지만 pHash 너무 멀다
    groups = cluster_similar(db, cfg=Config())
    assert not any({a, b} <= {fid for fid, _ in g} for g in groups)


def test_set_exif_dates(tmp_path):
    db = Database(tmp_path / "lib.db")
    db.add_file("/r/a.jpg", 100, 1.0, "jpg")
    fid = db.conn.execute("SELECT id FROM files WHERE path=?", ("/r/a.jpg",)).fetchone()["id"]
    db.set_exif_dates([(1234.5, fid)])
    row = db.conn.execute("SELECT exif_dt FROM files WHERE id=?", (fid,)).fetchone()
    assert row["exif_dt"] == 1234.5
