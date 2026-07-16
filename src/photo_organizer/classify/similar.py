"""유사 그룹 클러스터링 (docs/SPEC.md 4.3 [4]).

pHash 해밍 거리가 임계값 이내인 이미지를 같은 그룹으로 묶는다. 10만 장에서
O(N²) 전수 비교를 피하려고 **BK-tree**로 임계값 이웃만 조회하고, union-find로
연결 성분을 그룹으로 만든다.

이 단계는 DB의 pHash만 사용하며 이미지를 다시 읽지 않는다.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..core.config import Config
from ..core.database import Database


def hamming(a_hex: str, b_hex: str) -> int:
    """두 16진 해시 문자열의 해밍 거리(다른 비트 수)."""
    return bin(int(a_hex, 16) ^ int(b_hex, 16)).count("1")


class BKTree:
    """이산 거리(해밍) 공간의 근접 이웃 검색용 BK-tree.

    노드는 (file_id, phash_hex). 삽입/조회 모두 거리 함수만 사용한다.
    삼각 부등식으로 조회 시 후보 가지를 [d-threshold, d+threshold]로 제한한다.
    """

    def __init__(self) -> None:
        self._root: Optional[tuple] = None  # (item, {dist: child_node})

    def add(self, item: tuple[int, str]) -> None:
        if self._root is None:
            self._root = (item, {})
            return
        node = self._root
        while True:
            term, children = node
            d = hamming(item[1], term[1])
            child = children.get(d)
            if child is None:
                children[d] = (item, {})
                return
            node = child

    def query(self, phash_hex: str, threshold: int) -> list[tuple[int, int]]:
        """threshold 이내의 (distance, file_id) 목록을 반환한다."""
        if self._root is None:
            return []
        results: list[tuple[int, int]] = []
        stack = [self._root]
        while stack:
            (fid, term_hex), children = stack.pop()
            d = hamming(phash_hex, term_hex)
            if d <= threshold:
                results.append((d, fid))
            lo, hi = d - threshold, d + threshold
            for dist, child in children.items():
                if lo <= dist <= hi:
                    stack.append(child)
        return results


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
    entries = [(r["id"], r["phash"]) for r in db.iter_phashes() if r["phash"]]
    if progress is not None:
        progress(f"BK-tree 구축: {len(entries)}개")

    tree = BKTree()
    uf = _UnionFind()
    for item in entries:
        tree.add(item)
        uf.add(item[0])

    # 각 노드의 임계 이웃을 union
    for fid, ph in entries:
        for _dist, nid in tree.query(ph, cfg.hamming_threshold):
            if nid != fid:
                uf.union(fid, nid)

    # 연결 성분 → 그룹
    comps: dict[int, list[tuple[int, str]]] = {}
    hex_by_id = dict(entries)
    for fid, ph in entries:
        comps.setdefault(uf.find(fid), []).append((fid, ph))

    groups: list[list[tuple[int, float]]] = []
    for members in comps.values():
        if len(members) < 2:
            continue
        rep_id = min(m[0] for m in members)
        rep_hex = hex_by_id[rep_id]
        scored = [
            (fid, round(1.0 - hamming(ph, rep_hex) / cfg.phash_bits, 4))
            for fid, ph in members
        ]
        groups.append(scored)

    if progress is not None:
        progress(f"유사 그룹 {len(groups)}개")
    db.save_similar_groups(groups)
    return groups
