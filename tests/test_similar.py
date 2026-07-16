"""유사도 클러스터링 테스트 — 해밍 거리, BK-tree, 그룹핑."""
import pytest

from photo_organizer.classify.similar import BKTree, hamming


def test_hamming_basic():
    assert hamming("00", "00") == 0
    assert hamming("00", "01") == 1
    assert hamming("0f", "00") == 4
    assert hamming("ff", "00") == 8


def test_hamming_symmetric():
    a, b = "a1b2c3d4", "a1b2c3d5"
    assert hamming(a, b) == hamming(b, a)


def test_bktree_finds_within_threshold():
    tree = BKTree()
    items = [
        (1, "0000"),
        (2, "0001"),  # dist 1 from #1
        (3, "000f"),  # dist 3 from #1
        (4, "ffff"),  # dist 16 from #1
    ]
    for it in items:
        tree.add(it)

    within2 = {fid for _d, fid in tree.query("0000", 2)}
    assert within2 == {1, 2}  # 3은 거리3, 4는 거리16 → 제외

    within4 = {fid for _d, fid in tree.query("0000", 4)}
    assert within4 == {1, 2, 3}


def test_bktree_empty():
    assert BKTree().query("0000", 5) == []


def test_bktree_matches_bruteforce():
    """BK-tree 조회 결과가 전수 비교와 일치해야 한다(정확성)."""
    # 결정적 의사난수 해시 생성 (Math.random 등 불필요).
    items = [(i, format((i * 2654435761) & 0xFFFFFFFF, "08x")) for i in range(50)]
    tree = BKTree()
    for it in items:
        tree.add(it)

    threshold = 8
    for _fid, ph in items:
        got = {fid for _d, fid in tree.query(ph, threshold)}
        expected = {fid for fid, h in items if hamming(ph, h) <= threshold}
        assert got == expected
