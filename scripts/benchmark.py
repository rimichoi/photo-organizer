"""10만 장 규모 성능/부하 벤치마크 (docs/HANDOFF.md Phase 5).

전체 파이프라인을 100k 실제 이미지로 돌리는 것은 analyze(디코드+썸네일)·dedup(파일 해시)
I/O가 비현실적으로 무겁다. 그래서 하이브리드로 측정한다:

- 알고리즘 단계(진짜 스케일링 리스크)는 합성 DB로 100k 측정:
  유사 클러스터링(BK-tree + union-find + 버스트 O(N²)), protected_survivors 전체 스캔,
  리포트 쿼리, DB 크기, 피크 RSS.
- I/O 단계(scan)는 빈 파일 생성으로 실측. analyze/dedup은 실제 이미지가 필요하므로
  소규모(sample_photos)로 per-item 비용만 재고 외삽은 사용자 판단에 맡긴다.

사용:
    PYTHONPATH=src .venv/bin/python scripts/benchmark.py --n 100000 --scan-n 100000
    PYTHONPATH=src .venv/bin/python scripts/benchmark.py --n 10000 --scan-n 0   # 빠른 확인
"""
from __future__ import annotations

import argparse
import os
import random
import resource
import sys
import tempfile
import time
from contextlib import contextmanager

from photo_organizer.core.config import Config
from photo_organizer.core.database import Database
from photo_organizer.classify.similar import cluster_similar
from photo_organizer.core.scanner import scan_directory

_IS_MAC = sys.platform == "darwin"


def _rss_mb() -> float:
    """현재 프로세스 피크 RSS(MB). macOS는 bytes, Linux는 KB 단위."""
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if _IS_MAC else r / 1024


@contextmanager
def _timed(label: str, results: dict):
    t0 = time.perf_counter()
    yield
    dt = time.perf_counter() - t0
    results[label] = dt
    print(f"  {label:38s} {dt:8.2f}s   (peak RSS {_rss_mb():7.1f} MB)")


def _rand_hash() -> int:
    return random.getrandbits(64)


def _perturb(base: int, k: int) -> int:
    for b in random.sample(range(64), k):
        base ^= (1 << b)
    return base


def populate_synthetic(db: Database, n: int, cfg: Config) -> None:
    """n개 합성 files 행을 삽입한다. 실제 부하 재현을 위해 구조를 넣는다:

    - pHash 클러스터: 4개씩 같은 base에서 strict 임계값 이내로 흔들어 유사 그룹 형성.
    - 버스트: 클러스터 내 timestamp를 1초 간격으로(촬영시각 근접).
    - 밀집 블록: 마지막 2000개는 같은 timestamp로 몰아 버스트 O(N²) 최악을 측정.
    - 일부(20%)는 랜덤 해시 싱글턴(이웃 없음)으로 BK-tree 탐색 다양성 확보.
    """
    thr = cfg.hamming_threshold  # 5
    rows = []
    i = 0
    base_ts = 1_600_000_000.0
    while i < n:
        if random.random() < 0.20:
            # 싱글턴
            h = _rand_hash()
            rows.append((f"/bench/s{i}.jpg", 1000 + i, 0.0, "jpg",
                         f"{h:016x}", base_ts + i * 100, "analyzed"))
            i += 1
        else:
            # 4개 클러스터 (strict 이내), timestamp 1초 간격(버스트)
            base = _rand_hash()
            ts = base_ts + i * 100
            for j in range(4):
                if i >= n:
                    break
                h = base if j == 0 else _perturb(base, random.randint(1, thr))
                rows.append((f"/bench/c{i}.jpg", 1000 + i, 0.0, "jpg",
                             f"{h:016x}", ts + j, "analyzed"))
                i += 1
    # 밀집 timestamp 블록: 마지막 min(2000, n) 행의 exif_dt를 동일 값으로 덮어 O(N²) 자극
    dense = min(2000, n)
    for k in range(dense):
        r = rows[n - 1 - k]
        rows[n - 1 - k] = r[:5] + (base_ts + 5_000_000.0,) + r[6:]

    with db.batch() as conn:
        conn.executemany(
            "INSERT INTO files(path, size, mtime, format, phash, exif_dt, scan_status) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    print(f"  populated {len(rows):,} synthetic rows "
          f"(dense same-ts block: {dense:,})")


def bench_algorithms(n: int, results: dict) -> None:
    cfg = Config()
    tmp = tempfile.mkdtemp(prefix="pobench_")
    db_path = os.path.join(tmp, "bench.db")
    db = Database(db_path)
    print(f"\n[알고리즘 단계 — 합성 DB, N={n:,}]")
    with _timed("populate (setup)", results):
        populate_synthetic(db, n, cfg)

    with _timed("similar clustering (BK-tree+union+burst)", results):
        groups = cluster_similar(db, cfg=cfg)
    print(f"    → 유사 그룹 {len(groups):,}개")

    # protected_survivors: 대규모 제거 요청(모든 파일) 시 그룹 테이블 전체 스캔 비용
    all_ids = [r["id"] for r in db.conn.execute("SELECT id FROM files")]
    with _timed("protected_survivors (all ids)", results):
        prot = db.protected_survivors(all_ids)
    print(f"    → 보호 대상 {len(prot):,}개")

    with _timed("report query (materialize similar)", results):
        rep = list(db.iter_similar_groups())
    print(f"    → 리포트 행 {len(rep):,}개")

    db.close()
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            results.setdefault("db_bytes", 0)
            results["db_bytes"] += os.path.getsize(p)
    print(f"  DB 크기: {results.get('db_bytes', 0) / (1024*1024):.1f} MB")


def bench_scan(scan_n: int, results: dict) -> None:
    if scan_n <= 0:
        return
    tmp = tempfile.mkdtemp(prefix="poscan_")
    root = os.path.join(tmp, "photos")
    print(f"\n[스캔 단계 — 빈 파일 {scan_n:,}개, 1000개/폴더]")
    with _timed("create empty files (setup)", results):
        per_dir = 1000
        for d in range((scan_n + per_dir - 1) // per_dir):
            sub = os.path.join(root, f"dir{d:03d}")
            os.makedirs(sub, exist_ok=True)
            for k in range(min(per_dir, scan_n - d * per_dir)):
                open(os.path.join(sub, f"img{d}_{k}.jpg"), "w").close()

    db = Database(os.path.join(tmp, "scan.db"))
    with _timed("scan_directory (discovery)", results):
        summary = scan_directory(db, root)
    print(f"    → 발견 {summary['new']:,}개")

    # 재스캔(무변경) 비용 — 증분 upsert의 unchanged 경로
    with _timed("re-scan (all unchanged)", results):
        summary2 = scan_directory(db, root)
    print(f"    → unchanged {summary2['unchanged']:,}개")

    # 삭제 감지 재스캔(paths_under_root + 대조)
    with _timed("re-scan --detect-deletions", results):
        summary3 = scan_directory(db, root, detect_deletions=True)
    print(f"    → deleted {summary3['deleted']}")
    db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="photo-organizer 성능 벤치마크")
    ap.add_argument("--n", type=int, default=100_000, help="합성 DB 행 수 (기본 100k)")
    ap.add_argument("--scan-n", type=int, default=100_000, help="스캔용 빈 파일 수 (0=생략)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    print(f"=== photo-organizer 벤치마크 (N={args.n:,}, scan-n={args.scan_n:,}) ===")
    print(f"플랫폼: {sys.platform}, python {sys.version.split()[0]}")
    results: dict = {}
    t0 = time.perf_counter()
    bench_algorithms(args.n, results)
    bench_scan(args.scan_n, results)
    print(f"\n총 소요: {time.perf_counter() - t0:.1f}s, 최종 피크 RSS {_rss_mb():.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
