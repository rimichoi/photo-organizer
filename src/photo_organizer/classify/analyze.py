"""파일 분석 오케스트레이션 (docs/SPEC.md 4.3 [3]+[5], 5.1 read-once).

각 이미지를 '한 번만' 열어 pHash/dHash·썸네일·규칙 분류를 동시에 추출한다
(네트워크 재접근 최소화). CPU/I/O 바운드 작업은 multiprocessing 워커에서
수행하고, SQLite 쓰기는 단일 라이터인 메인 프로세스가 배치로 처리한다.

워커 함수는 모듈 최상위에 두어 Windows/macOS의 spawn 방식에서 피클링된다.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from ..core.config import Config
from ..core.database import Database


def _analyze_task(args: tuple) -> dict:
    """워커: (file_id, path, thumb_dir, cfg_dict)를 받아 분석 결과 dict를 반환.

    import는 spawn 워커에서의 안전을 위해 함수 안에서 수행한다.
    """
    file_id, path, thumb_dir, cfg_dict = args
    from ..core.image_loader import load_analyzed
    from . import phash, rules

    cfg = Config.from_dict(cfg_dict)
    img, meta = load_analyzed(path)
    if img is None:
        return {"file_id": file_id, "status": "error", "error": "이미지 로드 실패"}
    try:
        ph, dh = phash.compute_hashes(img)
        thumb_path = phash.save_thumbnail(img, thumb_dir, file_id, cfg.thumb_size)
        category, confidence = rules.classify(img, os.path.basename(path), meta, cfg)
    except Exception as exc:  # 개별 파일 오류가 전체를 막지 않는다(NFR-03)
        return {"file_id": file_id, "status": "error", "error": str(exc)[:200]}
    return {
        "file_id": file_id,
        "status": "analyzed",
        "phash": ph,
        "dhash": dh,
        "thumb_path": thumb_path,
        "category": category,
        "confidence": confidence,
        "exif_dt": meta.get("dt"),
    }


def run_analyze(
    db: Database,
    thumb_dir: str,
    workers: int = 1,
    cfg: Optional[Config] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int]:
    """phash가 없는 파일들을 분석해 DB에 기록한다. (성공 수, 오류 수) 반환.

    ``scan_status`` 기준으로 미처리 파일만 대상이라 중단 후 재개 시 이어서 처리한다.
    """
    cfg = cfg or Config()
    os.makedirs(thumb_dir, exist_ok=True)

    entries = [(r["id"], r["path"]) for r in db.iter_files_needing_analysis()]
    total = len(entries)
    if total == 0:
        return 0, 0

    tasks = [(fid, path, thumb_dir, cfg.to_dict()) for fid, path in entries]

    if workers > 1 and total > 1:
        from multiprocessing import Pool

        with Pool(processes=workers) as pool:
            results = pool.map(_analyze_task, tasks)
    else:
        results = [_analyze_task(t) for t in tasks]

    updates: list[tuple] = []
    errors: list[tuple[int, str]] = []
    for res in results:
        if res["status"] == "error":
            errors.append((res["file_id"], res["error"]))
        else:
            updates.append((
                res["phash"], res["dhash"], res["thumb_path"],
                res["category"], res["confidence"], res["file_id"],
            ))

    db.set_analysis_results(updates)
    dated = [(res["exif_dt"], res["file_id"]) for res in results
             if res["status"] != "error" and res.get("exif_dt") is not None]
    db.set_exif_dates(dated)
    db.mark_errors(errors)
    if progress is not None:
        progress(len(updates), len(errors))
    return len(updates), len(errors)
