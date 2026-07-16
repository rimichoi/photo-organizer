"""Phase 1 CLI — 코어 엔진(스캔·완전중복)을 GUI 없이 검증한다.

사용 예:
    photo-organizer-cli --db lib.db scan "/Volumes/NAS/photos"
    photo-organizer-cli --db lib.db dedup --workers 4
    photo-organizer-cli --db lib.db analyze --workers 4   # pHash+썸네일+분류
    photo-organizer-cli --db lib.db similar --threshold 5 # 유사 그룹
    photo-organizer-cli --db lib.db report --csv duplicates.csv

Windows/macOS 공통. dedup가 multiprocessing.Pool을 쓰므로 진입점은 반드시
``if __name__ == "__main__"`` 가드 아래에서 호출해야 한다(spawn 안전).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .classify.analyze import run_analyze
from .classify.bestshot import run_bestshot
from .classify.similar import cluster_similar
from .core.config import Config
from .core.database import Database
from .core.hasher import find_exact_duplicates
from .core.scanner import scan_directory

_DEFAULT_THUMB_DIR = "thumbnails"


def _cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.exists():
        print(f"경로를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    def progress(n: int) -> None:
        print(f"\r  발견: {n:,}개", end="", flush=True)

    with Database(args.db) as db:
        count = scan_directory(db, root, progress=progress)
    print(f"\n스캔 완료: 이미지 {count:,}개 기록 (DB: {args.db})")
    return 0


def _cmd_dedup(args: argparse.Namespace) -> int:
    def progress(msg: str) -> None:
        print(f"  {msg}")

    with Database(args.db) as db:
        groups = find_exact_duplicates(
            db,
            workers=args.workers,
            quick_prefilter=not args.no_prefilter,
            progress=progress,
        )
    total_dupes = sum(len(items) - 1 for items in groups.values())
    print(
        f"완전 중복 검출 완료: {len(groups)}개 그룹, "
        f"여분 파일 {total_dupes:,}개 (대표 1개씩 유지 기준)"
    )
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    thumb_dir = args.thumb_dir or _DEFAULT_THUMB_DIR
    print(f"분석 시작 (썸네일: {thumb_dir}, workers={args.workers})…")
    with Database(args.db) as db:
        ok, err = run_analyze(db, thumb_dir, workers=args.workers, cfg=cfg)
        counts = db.category_counts()
    print(f"분석 완료: 성공 {ok:,}개, 오류 {err:,}개")
    if counts:
        summary = " · ".join(f"{k} {v:,}" for k, v in counts.items())
        print(f"  분류: {summary}")
    return 0


def _cmd_similar(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    if args.threshold is not None:
        cfg.hamming_threshold = args.threshold

    def progress(msg: str) -> None:
        print(f"  {msg}")

    with Database(args.db) as db:
        groups = cluster_similar(db, cfg=cfg, progress=progress)
    total_members = sum(len(g) for g in groups)
    print(
        f"유사 그룹 검출 완료: {len(groups)}개 그룹, "
        f"총 {total_members:,}장 (해밍 임계값 {cfg.hamming_threshold})"
    )
    return 0


def _cmd_bestshot(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)

    def progress(msg: str) -> None:
        print(f"  {msg}")

    with Database(args.db) as db:
        n = run_bestshot(db, cfg=cfg, preset=args.preset, progress=progress)
    print(f"베스트샷 선정 완료: {n}개 그룹")
    return 0


def _write_csv(path: str, header: list[str], rows: list[list]) -> None:
    # CLAUDE.md 전역 규칙: 한글 CSV는 UTF-8 BOM. utf-8-sig가 BOM을 붙이고,
    # csv 모듈이 콤마/따옴표를 자동 이스케이프한다.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def _write_json(path: str, header: list[str], rows: list[list]) -> None:
    """header를 키로 하는 객체 배열로 JSON 저장 (UTF-8, 한글 그대로)."""
    data = [dict(zip(header, r)) for r in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _export(out_path: str, fmt: str, header: list[str], data: list[list], label: str) -> None:
    (_write_csv if fmt == "csv" else _write_json)(out_path, header, data)
    print(f"{label} {fmt.upper()} 저장: {out_path} ({len(data)}행)")


def _report_duplicates(db: Database, out_path: str | None, fmt: str | None) -> None:
    rows = list(db.iter_duplicate_groups())
    if not rows:
        print("중복 그룹이 없습니다. (scan → dedup 실행 필요)")
        return
    group_ids = {r["group_id"] for r in rows}
    if out_path:
        _export(
            out_path, fmt,
            ["group_id", "is_representative", "size", "content_hash", "path"],
            [[r["group_id"], "대표" if r["is_representative"] else "",
              r["size"], r["content_hash"], r["path"]] for r in rows],
            "완전중복",
        )
        return
    current = None
    for r in rows:
        if r["group_id"] != current:
            current = r["group_id"]
            print(f"\n[중복 {current}] size={r['size']:,}  hash={r['content_hash'][:12]}…")
        mark = "★ 대표" if r["is_representative"] else "  중복"
        print(f"  {mark}  {r['path']}")
    print(f"\n완전중복: {len(group_ids)}개 그룹")


def _report_similar(db: Database, out_path: str | None, fmt: str | None) -> None:
    rows = list(db.iter_similar_groups())
    if not rows:
        print("유사 그룹이 없습니다. (analyze → similar 실행 필요)")
        return
    if out_path:
        _export(
            out_path, fmt,
            ["group_id", "is_best_shot", "quality_score", "similarity_score", "category", "path"],
            [[r["group_id"], "베스트샷" if r["is_best_shot"] else "",
              r["quality_score"], r["similarity_score"], r["category"] or "", r["path"]]
             for r in rows],
            "유사그룹",
        )
        return
    current = None
    for r in rows:
        if r["group_id"] != current:
            current = r["group_id"]
            print(f"\n[유사 {current}]")
        cat = f" [{r['category']}]" if r["category"] else ""
        mark = "⭐ 베스트" if r["is_best_shot"] else "        "
        reason = ""
        if r["quality_detail"]:
            try:
                reason = f"  ({json.loads(r['quality_detail']).get('reason', '')})"
            except (ValueError, TypeError):
                reason = ""
        print(f"  {mark} 유사도 {r['similarity_score']:.3f}{cat}  "
              f"{r['path'].split('/')[-1]}{reason}")
    print(f"\n유사그룹: {len(group_ids)}개 그룹")


def _cmd_report(args: argparse.Namespace) -> int:
    out_path = args.csv or args.json
    fmt = "csv" if args.csv else ("json" if args.json else None)
    if out_path and args.kind == "all":
        print("파일 출력은 --kind dup 또는 --kind similar 와 함께 사용하세요.", file=sys.stderr)
        return 2
    with Database(args.db) as db:
        total_files = db.count_files()
        if args.kind in ("dup", "all"):
            _report_duplicates(db, out_path if args.kind == "dup" else None, fmt)
        if args.kind in ("similar", "all"):
            _report_similar(db, out_path if args.kind == "similar" else None, fmt)
        if args.kind == "all":
            counts = db.category_counts()
            if counts:
                summary = " · ".join(f"{k} {v:,}" for k, v in counts.items())
                print(f"\n분류: {summary}")
    print(f"\n전체 파일 {total_files:,}개")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-organizer-cli",
        description="Photo Organizer 코어 엔진 (스캔·완전중복·유사·분류)",
    )
    parser.add_argument(
        "--db", default="photo_organizer.db", help="SQLite DB 경로 (기본: photo_organizer.db)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="디렉토리를 재귀 스캔해 이미지를 DB에 기록")
    p_scan.add_argument("path", help="스캔할 루트 경로 (로컬/네트워크 마운트)")
    p_scan.set_defaults(func=_cmd_scan)

    p_dedup = sub.add_parser("dedup", help="완전 중복(byte 동일) 검출")
    p_dedup.add_argument(
        "--workers", type=int, default=1, help="해시 병렬 프로세스 수 (기본: 1)"
    )
    p_dedup.add_argument(
        "--no-prefilter", action="store_true", help="빠른 해시 사전필터 비활성화"
    )
    p_dedup.set_defaults(func=_cmd_dedup)

    p_analyze = sub.add_parser("analyze", help="pHash·썸네일 생성 + 규칙 분류")
    p_analyze.add_argument("--workers", type=int, default=1, help="병렬 프로세스 수 (기본: 1)")
    p_analyze.add_argument("--thumb-dir", help=f"썸네일 폴더 (기본: {_DEFAULT_THUMB_DIR})")
    p_analyze.add_argument("--config", help="임계값 TOML 설정 파일")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_similar = sub.add_parser("similar", help="유사 그룹 클러스터링 (BK-tree)")
    p_similar.add_argument("--threshold", type=int, help="해밍 거리 임계값 (기본: 설정값 5)")
    p_similar.add_argument("--config", help="임계값 TOML 설정 파일")
    p_similar.set_defaults(func=_cmd_similar)

    p_best = sub.add_parser("bestshot", help="유사 그룹 내 베스트샷 선정(품질 점수)")
    p_best.add_argument(
        "--preset", choices=["person", "landscape"],
        help="가중치 프리셋 (기본: 그룹에 얼굴 있으면 person, 없으면 landscape 자동)",
    )
    p_best.add_argument("--config", help="가중치 TOML 설정 파일")
    p_best.set_defaults(func=_cmd_bestshot)

    p_report = sub.add_parser("report", help="리포트 (콘솔 또는 CSV)")
    p_report.add_argument(
        "--kind", choices=["dup", "similar", "all"], default="all",
        help="리포트 종류 (기본: all). CSV 출력은 dup 또는 similar만 가능",
    )
    p_report.add_argument("--csv", help="CSV 출력 경로 (--kind dup|similar 와 함께)")
    p_report.add_argument("--json", help="JSON 출력 경로 (--kind dup|similar 와 함께)")
    p_report.set_defaults(func=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
