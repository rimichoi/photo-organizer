"""유사도 클러스터링 테스트 — 해밍 거리, 멀티인덱스 이웃 탐색, 그룹핑."""
import pytest

from photo_organizer.classify.similar import hamming, pairs_within_threshold


def test_hamming_basic():
    assert hamming("00", "00") == 0
    assert hamming("00", "01") == 1
    assert hamming("0f", "00") == 4
    assert hamming("ff", "00") == 8


def test_hamming_symmetric():
    a, b = "a1b2c3d4", "a1b2c3d5"
    assert hamming(a, b) == hamming(b, a)


def _pairs_set(entries, threshold, bits):
    """양방향 정규화한 쌍 집합 (a<b)."""
    out = set()
    for a, b in pairs_within_threshold(entries, threshold, bits):
        out.add((a, b) if a < b else (b, a))
    return out


def test_pairs_within_threshold_matches_bruteforce():
    """멀티인덱스 해싱 결과가 전수 비교와 정확히 일치해야 한다(거짓 음성 없음)."""
    # 결정적 의사난수 32비트 해시.
    entries = [(i, (i * 2654435761) & 0xFFFFFFFF) for i in range(80)]
    bits = 32
    for threshold in (0, 3, 8, 12):
        got = _pairs_set(entries, threshold, bits)
        expected = set()
        for ia in range(len(entries)):
            fa, ha = entries[ia]
            for ib in range(ia + 1, len(entries)):
                fb, hb = entries[ib]
                if bin(ha ^ hb).count("1") <= threshold:
                    expected.add((fa, fb) if fa < fb else (fb, fa))
        assert got == expected, f"threshold={threshold}"


def test_pairs_within_threshold_finds_close_pair():
    # 거리 1인 쌍은 어떤 밴딩에서도 반드시 검출.
    entries = [(1, 0x0000), (2, 0x0001), (3, 0xFFFF)]
    got = _pairs_set(entries, 2, 16)
    assert (1, 2) in got and (1, 3) not in got and (2, 3) not in got


def test_pairs_within_threshold_empty():
    assert list(pairs_within_threshold([], 5, 64)) == []
