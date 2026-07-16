"""백그라운드 워커 (docs/SPEC.md NFR-04 응답성).

무거운 파이프라인(스캔→중복→분석→유사→베스트샷)을 QThread에서 실행하고,
진행 상황을 시그널로 UI에 전달한다. UI 스레드는 절대 블로킹되지 않는다.

QObject를 별도 QThread로 moveToThread 하는 표준 패턴을 쓴다.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..classify.analyze import run_analyze
from ..classify.bestshot import run_bestshot
from ..classify.similar import cluster_similar
from ..core.config import Config
from ..core.database import Database
from ..core.hasher import find_exact_duplicates
from ..core.scanner import scan_directory


class PipelineWorker(QObject):
    """전체 정리 파이프라인을 순차 실행하는 워커."""

    progress = Signal(str)       # 사람이 읽는 진행 메시지
    finished = Signal(dict)      # 완료 요약
    failed = Signal(str)         # 오류 메시지

    def __init__(
        self,
        db_path: str,
        root: str,
        thumb_dir: str,
        workers: int = 1,
        cfg: Config | None = None,
    ):
        super().__init__()
        self._db_path = db_path
        self._root = root
        self._thumb_dir = thumb_dir
        self._workers = workers
        self._cfg = cfg or Config()

    def run(self) -> None:
        """스레드에서 호출된다. 각 단계 완료 시 progress를 emit."""
        try:
            with Database(self._db_path) as db:
                self.progress.emit("① 스캔 시작…")
                scanned = scan_directory(
                    db, self._root,
                    progress=lambda c: self.progress.emit(f"① 스캔 중… {c:,}개 발견"),
                )

                self.progress.emit("② 완전 중복 검출 중…")
                dups = find_exact_duplicates(db, workers=self._workers)

                self.progress.emit("③ 분석(해시·썸네일·분류) 중…")
                ok, err = run_analyze(
                    db, self._thumb_dir, workers=self._workers, cfg=self._cfg,
                )

                self.progress.emit("④ 유사 그룹 클러스터링 중…")
                sims = cluster_similar(db, cfg=self._cfg)

                self.progress.emit("⑤ 베스트샷 선정 중…")
                best_groups = run_bestshot(db, cfg=self._cfg)

                summary = {
                    "scanned": scanned,
                    "analyzed_ok": ok,
                    "analyzed_err": err,
                    "duplicate_groups": len(dups),
                    "similar_groups": len(sims),
                    "bestshot_groups": best_groups,
                }
            self.progress.emit("완료")
            self.finished.emit(summary)
        except Exception as exc:  # 파이프라인 오류를 UI로 전달(크래시 방지)
            self.failed.emit(str(exc))
