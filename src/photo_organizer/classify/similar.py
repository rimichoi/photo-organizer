"""유사 그룹 클러스터링 (docs/SPEC.md 4.3 [4]).

pHash 해밍 거리가 임계값 이내인 이미지를 같은 그룹으로 묶는다. 10만 장에서
O(N²) 전수 비교를 피하려고 **멀티인덱스 해싱**(해시를 임계값+1개 밴드로 나눠
비둘기집 원리로 후보를 버킷팅)으로 임계 이웃 쌍만 찾고, union-find로 연결
성분을 그룹으로 만든다. (BK-tree는 균일 해시의 평균거리 부근에서 가지치기가
실패해 10만 장 규모에서 느렸다 — 벤치마크로 확인 후 교체.)

이 단계는 DB의 pHash만 사용하며 이미지를 다시 읽지 않는다.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional

from ..core.config import Config
from ..core.database import Database


def _ham(a: int, b: int) -> int:
    """정수 해시 간 해밍 거리 (핫패스). int.bit_count는 3.10+."""
    return (a ^ b).bit_count()


def hamming(a_hex: str, b_hex: str) -> int:
    """두 16진 해시 문자열의 해밍 거리(공개 API/테스트용)."""
    return _ham(int(a_hex, 16), int(b_hex, 16))


def _band_ranges(bits: int, n_bands: int) -> list[tuple[int, int]]:
    """bits 폭 해시를 n_bands개 밴드로 쪼갠 (shift, mask) 목록. 폭은 최대한 균등."""
    base, rem = divmod(bits, n_bands)
    ranges: list[tuple[int, int]] = []
    shift = 0
    for i in range(n_bands):
        w = base + (1 if i < rem else 0)
        ranges.append((shift, (1 << w) - 1))
        shift += w
    return ranges


def pairs_within_threshold(
    entries: list[tuple[int, int]], threshold: int, bits: int = 64
):
    """해밍 거리가 threshold 이내인 (fid_a, fid_b) 쌍을 모두 yield 한다.

    멀티인덱스 해싱: 해시를 threshold+1개 밴드로 나눈다. 두 해시가 threshold
    이내로 다르면 비둘기집 원리상 최소 한 밴드는 완전히 같으므로, 밴드 값으로
    버킷팅해 같은 버킷의 후보만 실제 거리를 검증한다(BK-tree의 평균거리 부근
    가지치기 실패를 피해 10만 장에서 near-linear). 참 쌍이 여러 밴드에서 중복
    yield될 수 있으나 호출부 union이 멱등이라 무해하다(전역 dedup set은 메모리
    절약을 위해 생략). entries는 (file_id, phash_int) 목록.
    """
    if threshold < 0 or not entries:
        return
    ranges = _band_ranges(bits, threshold + 1)
    for shift, mask in ranges:
        buckets: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for fid, h in entries:
            buckets[(h >> shift) & mask].append((fid, h))
        for bucket in buckets.values():
            m = len(bucket)
            if m < 2:
                continue
            for a in range(m):
                fa, ha = bucket[a]
                for b in range(a + 1, m):
                    fb, hb = bucket[b]
                    if _ham(ha, hb) <= threshold:
                        yield fa, fb


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def add(self, x: int) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # 경로 압축
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_similar(
    db: Database,
    cfg: Optional[Config] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> list[list[tuple[int, float]]]:
    """유사 그룹을 검출해 DB에 저장하고, 그룹 목록을 반환한다.

    각 그룹은 [(file_id, similarity_score), ...] (원소 2개 이상). similarity_score는
    그룹 대표(최소 id)와의 해밍 거리를 0~1로 정규화한 값(대표=1.0).
    """
    cfg = cfg or Config()
    rows = list(db.iter_phashes())
    # pHash를 여기서 1회만 int로 변환하고, 이후 모든 비교는 정수로 수행한다
    # (hex 재파싱 제거 — 10만 장에서 클러스터링 병목의 핵심 상수배 개선).
    entries = [(r["id"], int(r["phash"], 16)) for r in rows if r["phash"]]
    dt_by_id = {r["id"]: r["exif_dt"] for r in rows if r["phash"]}
    int_by_id = dict(entries)
    if progress is not None:
        progress(f"이웃 탐색: {len(entries)}개")

    uf = _UnionFind()
    for fid, _h in entries:
        uf.add(fid)

    # 멀티인덱스 해싱으로 임계 이웃 쌍을 찾아 union (union은 멱등이라 중복 무해)
    for fa, fb in pairs_within_threshold(entries, cfg.hamming_threshold, cfg.phash_bits):
        uf.union(fa, fb)

    # 버스트 그룹핑: 촬영시각이 burst_seconds 이내이고 pHash 거리가 burst 임계값
    # 이내인 사진을 union (포즈가 달라 strict pHash로는 놓치는 연사 보완).
    # dated를 시간순 정렬해 내부 루프를 window 이탈 시 break — 같은 timestamp가
    # 대량이면 O(N^2)가 될 수 있으나, 정상 데이터에서 버스트는 시간상 작은
    # 군집이라 실질 저비용이다.
    dated = sorted(
        ((dt_by_id[fid], fid) for fid, _h in entries if dt_by_id.get(fid) is not None),
        key=lambda t: t[0],
    )
    for i in range(len(dated)):
        dt_i, fid_i = dated[i]
        h_i = int_by_id[fid_i]
        for j in range(i + 1, len(dated)):
            dt_j, fid_j = dated[j]
            if dt_j - dt_i > cfg.burst_seconds:
                break  # 시간 정렬됨 → 이후 후보 없음
            if _ham(h_i, int_by_id[fid_j]) <= cfg.burst_hamming_threshold:
                uf.union(fid_i, fid_j)

    # 연결 성분 → 그룹
    comps: dict[int, list[tuple[int, int]]] = {}
    for fid, h in entries:
        comps.setdefault(uf.find(fid), []).append((fid, h))

    groups: list[list[tuple[int, float]]] = []
    for members in comps.values():
        if len(members) < 2:
            continue
        rep_id = min(m[0] for m in members)
        rep_h = int_by_id[rep_id]
        scored = [
            (fid, round(1.0 - _ham(h, rep_h) / cfg.phash_bits, 4))
            for fid, h in members
        ]
        groups.append(scored)

    if progress is not None:
        progress(f"유사 그룹 {len(groups)}개")
    db.save_similar_groups(groups)
    return groups
