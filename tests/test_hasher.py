"""완전 중복 검출 테스트 (SPEC 6.1: 해시 일관성)."""
from photo_organizer.core.database import Database
from photo_organizer.core.hasher import find_exact_duplicates
from photo_organizer.core.scanner import scan_directory


def _write(path, data: bytes):
    path.write_bytes(data)


def _seed_db(tmp_path, files: dict):
    """{'name.jpg': bytes} 를 디스크에 쓰고 스캔한 DB를 돌려준다."""
    root = tmp_path / "photos"
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        _write(root / name, data)
    db = Database(tmp_path / "test.db")
    scan_directory(db, root)
    return db


def test_identical_content_grouped(tmp_path):
    """바이트가 동일한 파일들은 같은 중복 그룹으로 묶인다."""
    same = b"\xff\xd8\xff" + b"PHOTO-CONTENT" * 100  # 동일 크기·동일 내용
    db = _seed_db(tmp_path, {
        "a.jpg": same,
        "b.jpg": same,
        "c.jpg": same,
    })
    groups = find_exact_duplicates(db)
    assert len(groups) == 1
    (items,) = groups.values()
    assert len(items) == 3
    db.close()


def test_one_byte_difference_not_grouped(tmp_path):
    """크기는 같지만 1바이트 다르면 중복이 아니다 (전체 해시 단계에서 분리)."""
    base = b"PHOTO" * 100
    variant = base[:-1] + b"X"  # 같은 길이, 마지막 바이트만 다름
    db = _seed_db(tmp_path, {
        "a.jpg": base,
        "b.jpg": variant,
    })
    groups = find_exact_duplicates(db)
    assert groups == {}
    db.close()


def test_different_sizes_never_compared(tmp_path):
    """크기가 다르면 애초에 해시 후보에 오르지 않는다."""
    db = _seed_db(tmp_path, {
        "a.jpg": b"short",
        "b.jpg": b"a much longer content here",
    })
    groups = find_exact_duplicates(db)
    assert groups == {}
    db.close()


def test_prefilter_and_no_prefilter_agree(tmp_path):
    """빠른 해시 사전필터 유무와 관계없이 결과가 동일해야 한다."""
    same = b"DATA" * 500
    other = b"OTHR" * 500  # 같은 크기, 다른 내용
    files = {"a.jpg": same, "b.jpg": same, "c.jpg": other, "d.jpg": other}

    db1 = _seed_db(tmp_path / "p1", files)
    g_pre = find_exact_duplicates(db1, quick_prefilter=True)
    db1.close()

    db2 = _seed_db(tmp_path / "p2", files)
    g_no = find_exact_duplicates(db2, quick_prefilter=False)
    db2.close()

    # 그룹 수와 각 그룹 크기가 같아야 한다.
    assert sorted(len(v) for v in g_pre.values()) == sorted(len(v) for v in g_no.values())
    assert len(g_pre) == len(g_no) == 2


def test_representative_is_shortest_path(tmp_path):
    """대표 원본은 경로가 가장 짧은 파일로 결정적으로 선택된다."""
    same = b"REP-TEST" * 50
    root = tmp_path / "photos"
    (root / "deep" / "nested").mkdir(parents=True)
    _write(root / "top.jpg", same)               # 가장 짧은 경로
    _write(root / "deep" / "nested" / "x.jpg", same)
    db = Database(tmp_path / "t.db")
    scan_directory(db, root)
    find_exact_duplicates(db)

    rows = list(db.iter_duplicate_groups())
    rep = [r for r in rows if r["is_representative"]]
    assert len(rep) == 1
    assert rep[0]["path"].endswith("top.jpg")
    db.close()
