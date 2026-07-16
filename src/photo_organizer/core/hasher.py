"""완전 중복 검출기 — 바이트 단위로 동일한 파일 그룹을 찾는다.

docs/SPEC.md 4.3 [2] + 3.1 참조. 네트워크 I/O(파일 전체 읽기)가 가장 비싼
비용이므로 읽는 양을 단계적으로 줄인다:

  1. 크기(size)로 후보 축소 — DB에서 동일 크기 2개 이상만 대상(해시 0회).
  2. 앞부분 64KB만 읽는 빠른 해시로 세분화 — 대부분의 오탐을 전체 읽기 없이 배제.
  3. 빠른 해시가 충돌한 그룹만 전체 SHA-256 — 진짜 중복만 확정.

해시는 CPU/I/O 병렬화를 위해 multiprocessing.Pool로 분산할 수 있다
(``workers`` 인자). 함수는 모두 모듈 최상위라 Windows/macOS의 spawn 방식에서
안전하게 피클링된다. ``workers=1``이면 결정적인 직렬 실행(테스트에 적합).
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Callable, Optional

from .database import Database
from .platform_utils import normalize_long_path

# 빠른 해시가 읽는 선행 바이트 수.
_QUICK_BYTES = 64 * 1024
# 전체 해시의 스트리밍 블록 크기.
_BLOCK = 1024 * 1024


def _quick_hash(path: str) -> Optional[str]:
    """파일 앞부분 64KB의 SHA-256. 실패 시 None."""
    try:
        with open(normalize_long_path(path), "rb") as f:
            chunk = f.read(_QUICK_BYTES)
        return hashlib.sha256(chunk).hexdigest()
    except OSError:
        return None


def _full_hash(path: str) -> Optional[str]:
    """파일 전체의 SHA-256(스트리밍). 실패 시 None."""
    h = hashlib.sha256()
    try:
        with open(normalize_long_path(path), "rb") as f:
            for block in iter(lambda: f.read(_BLOCK), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def _quick_task(path: str) -> tuple[str, Optional[str]]:
    return path, _quick_hash(path)


def _full_task(path: str) -> tuple[str, Optional[str]]:
    return path, _full_hash(path)


def _run_hashes(
    paths: list[str],
    task,
    workers: int,
) -> dict[str, str]:
    """paths에 task(해시 함수)를 적용해 {경로: 해시} 맵을 만든다.

    실패(None)한 경로는 결과에서 제외한다. workers>1이면 프로세스 풀로 병렬.
    """
    if not paths:
        return {}
    if workers > 1 and len(paths) > 1:
        from multiprocessing import Pool

        with Pool(processes=workers) as pool:
            results = pool.map(task, paths)
    else:
        results = [task(p) for p in paths]
    return {p: h for p, h in results if h is not None}


def find_exact_duplicates(
    db: Database,
    workers: int = 1,
    quick_prefilter: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> dict[str, list[tuple[int, str]]]:
    """완전 중복 그룹을 검출해 DB에 저장하고, {content_hash: [(id, path)]}를 반환한다.

    반환 맵은 각 그룹의 원소가 2개 이상인 것만 포함한다(= 실제 중복).
    """
    # 1) 크기 기준 후보 수집 (동일 크기 그룹만).
    size_groups: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for row in db.iter_size_duplicates():
        size_groups[row["size"]].append((row["id"], row["path"]))

    if not size_groups:
        db.save_duplicate_groups({})
        return {}

    # 2) 빠른 해시로 (size, quick_hash) 버킷 세분화.
    if quick_prefilter:
        all_paths = [p for items in size_groups.values() for (_id, p) in items]
        if progress is not None:
            progress(f"빠른 해시 계산: {len(all_paths)}개 후보")
        quick = _run_hashes(all_paths, _quick_task, workers)
        buckets: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
        for size, items in size_groups.items():
            for fid, path in items:
                qh = quick.get(path)
                if qh is not None:
                    buckets[(size, qh)].append((fid, path))
        subgroups = [g for g in buckets.values() if len(g) > 1]
    else:
        subgroups = [g for g in size_groups.values() if len(g) > 1]

    # 3) 빠른 해시가 충돌한 그룹만 전체 SHA-256.
    full_paths = [p for g in subgroups for (_id, p) in g]
    if progress is not None:
        progress(f"전체 해시 계산: {len(full_paths)}개")
    full = _run_hashes(full_paths, _full_task, workers)

    # 4) 전체 해시로 최종 그룹핑 + files.content_hash 갱신.
    hash_groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    hash_updates: list[tuple[str, int]] = []
    for g in subgroups:
        for fid, path in g:
            ch = full.get(path)
            if ch is None:
                continue
            hash_groups[ch].append((fid, path))
            hash_updates.append((ch, fid))
    db.set_content_hashes(hash_updates)

    groups = {ch: items for ch, items in hash_groups.items() if len(items) > 1}
    db.save_duplicate_groups(groups)
    return groups
