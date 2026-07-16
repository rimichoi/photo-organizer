"""메인 윈도우 (docs/SPEC.md 4.2, Phase 4).

상단: 폴더 선택 · 정리 시작 · 새로 시작(초기화) · 썸네일 크기.
본문: 좌측 [완전 중복][유사·베스트샷][분류] 탭 + 우측 상세 패널(선택 항목 정보).
무거운 작업은 PipelineWorker(QThread)에 위임해 UI가 멈추지 않는다(NFR-04).

스캔은 '누적' 모델(같은 DB에 여러 폴더를 쌓는 라이브러리). '새로 시작' 버튼으로
전체를 비운다.
"""
from __future__ import annotations

import json
import os
import shutil

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSlider, QSplitter, QTabWidget, QVBoxLayout,
    QWidget,
)

from ..core import actions
from ..core.config import Config
from ..core.database import Database
from ..core.paths import default_db_path, default_quarantine_dir, default_thumb_dir
from .detail_panel import DetailPanel
from .thumbnail_grid import GroupedGrid, ThumbnailGrid
from .workers import PipelineWorker

_MIN_PX, _MAX_PX, _INIT_PX = 96, 320, 160


class MainWindow(QMainWindow):
    def __init__(self, db_path: str | None = None, thumb_dir: str | None = None):
        super().__init__()
        self._db_path = db_path or default_db_path()
        self._thumb_dir = thumb_dir or default_thumb_dir()
        self.__quarantine_dir: str | None = None
        self._root: str | None = None
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None

        self.setWindowTitle("Photo Organizer — 사진 정리")
        self.resize(1280, 820)
        self._build_ui()
        self.load_results()

    @property
    def _quarantine_dir(self) -> str:
        """격리 폴더 경로 — 실제 격리 동작 전까지 해석을 미뤄(지연 해석) 단순
        조회/구성만 하는 호출(테스트 포함)에서 사용자 데이터 폴더가 불필요하게
        생성되지 않게 한다."""
        if self.__quarantine_dir is None:
            self.__quarantine_dir = default_quarantine_dir()
        return self.__quarantine_dir

    # ---------- UI ----------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        # 상단 바
        top = QHBoxLayout()
        pick_btn = QPushButton("폴더 선택…")
        pick_btn.clicked.connect(self._pick_folder)
        self._start_btn = QPushButton("정리 시작")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_pipeline)
        self._reset_btn = QPushButton("새로 시작")
        self._reset_btn.clicked.connect(self._reset_all)
        self._undo_btn = QPushButton("되돌리기")
        self._undo_btn.setToolTip("격리로 이동한 마지막 작업을 원위치로 복구")
        self._undo_btn.clicked.connect(self._undo_last)

        self._folder_label = QLabel("스캔할 폴더를 선택하세요")
        hint_font = self._folder_label.font()
        hint_font.setItalic(True)
        self._folder_label.setFont(hint_font)

        top.addWidget(pick_btn)
        top.addWidget(self._start_btn)
        top.addWidget(self._reset_btn)
        top.addWidget(self._undo_btn)
        top.addWidget(self._folder_label, 1)
        top.addWidget(QLabel("크기"))
        self._zoom = QSlider(Qt.Horizontal)
        self._zoom.setRange(_MIN_PX, _MAX_PX)
        self._zoom.setValue(_INIT_PX)
        self._zoom.setFixedWidth(140)
        self._zoom.valueChanged.connect(self._on_zoom)
        top.addWidget(self._zoom)
        root.addLayout(top)

        # 진행률
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._status = QLabel("")
        root.addWidget(self._progress)
        root.addWidget(self._status)

        # 본문: 탭 + 상세 패널
        self._tabs = QTabWidget()
        self._dup_grid = GroupedGrid("완전 중복이 없습니다.\n폴더 선택 → 정리 시작")
        self._sim_grid = GroupedGrid("유사 사진 그룹이 없습니다.\n폴더 선택 → 정리 시작")
        self._cat_tab, self._cat_combo, self._cat_grid = self._build_category_tab()
        self._tabs.addTab(self._dup_grid, "완전 중복")
        self._tabs.addTab(self._sim_grid, "유사 · 베스트샷")
        self._tabs.addTab(self._cat_tab, "분류")

        self._detail = DetailPanel()
        for grid in (self._dup_grid, self._sim_grid, self._cat_grid):
            grid.selected.connect(self._detail.show_item)
            grid.action_requested.connect(self._do_action)
            grid.cull_requested.connect(self._quick_cull)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._tabs)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("준비됨")
        QShortcut(QKeySequence.Undo, self, activated=self._undo_last)

    def _build_category_tab(self) -> tuple[QWidget, QComboBox, ThumbnailGrid]:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("카테고리:"))
        combo = QComboBox()
        combo.currentTextChanged.connect(self._on_category_changed)
        bar.addWidget(combo, 1)
        lay.addLayout(bar)
        grid = ThumbnailGrid("분류된 사진이 없습니다.\n폴더 선택 → 정리 시작")
        lay.addWidget(grid, 1)
        return w, combo, grid

    # ---------- 동작 ----------
    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "스캔할 폴더 선택")
        if path:
            self._root = path
            self._folder_label.setText(path)
            f = self._folder_label.font(); f.setItalic(False); f.setBold(True)
            self._folder_label.setFont(f)
            self._start_btn.setEnabled(True)

    def _start_pipeline(self) -> None:
        if not self._root:
            return
        self._start_btn.setEnabled(False)
        self._reset_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("시작하는 중…")

        self._thread = QThread()
        self._worker = PipelineWorker(
            self._db_path, self._root, self._thumb_dir, workers=1, cfg=Config(),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _reset_all(self) -> None:
        ans = QMessageBox.question(
            self, "새로 시작",
            "지금까지의 스캔·분석 결과를 모두 지웁니다.\n(원본 사진은 삭제되지 않습니다)\n\n계속할까요?",
        )
        if ans != QMessageBox.Yes:
            return
        if os.path.exists(self._db_path):
            with Database(self._db_path) as db:
                db.reset()
        if os.path.isdir(self._thumb_dir):
            shutil.rmtree(self._thumb_dir, ignore_errors=True)
        self._detail.clear()
        self.load_results()
        self._status.setText("초기화됨 — 폴더를 선택해 다시 시작하세요")

    def _do_action(self, kind: str, file_ids: list) -> None:
        """휴지통/격리 이동 요청 처리 (확인 다이얼로그 → 실행 → 새로고침)."""
        if not file_ids:
            return
        n = len(file_ids)
        if kind == "trash":
            msg = f"{n}장을 OS 휴지통으로 보냅니다.\n(파인더/탐색기 휴지통에서 복구 가능)\n\n계속할까요?"
        else:
            msg = (f"{n}장을 격리 폴더('{self._quarantine_dir}')로 이동합니다.\n"
                   f"('되돌리기'로 복구 가능)\n\n계속할까요?")
        if QMessageBox.question(self, "정리 확인", msg) != QMessageBox.Yes:
            return
        with Database(self._db_path) as db:
            if kind == "trash":
                ok, failed, protected = actions.trash_files(db, file_ids)
                where = "휴지통"
            else:
                ok, failed, protected = actions.quarantine_files(db, file_ids, self._quarantine_dir)
                where = "격리 폴더"
        tail = f" (실패 {failed})" if failed else ""
        if protected:
            tail += f", {protected}장은 그룹 마지막 항목이라 보존"
        self._status.setText(f"{ok}장을 {where}(으)로 정리{tail}")
        self._detail.clear()
        self.load_results()

    def _quick_cull(self, file_ids: list) -> None:
        """키보드 컬링: 확인 다이얼로그 없이 즉시 격리(되돌리기 가능)."""
        if not file_ids:
            return
        with Database(self._db_path) as db:
            ok, failed, protected = actions.quarantine_files(
                db, file_ids, self._quarantine_dir
            )
        tail = f" (실패 {failed})" if failed else ""
        if protected:
            tail += f", {protected}장은 그룹 마지막이라 보존"
        self._status.setText(f"격리됨 {ok}장{tail} — 되돌리기(Ctrl+Z)")
        self._detail.clear()
        self.load_results()

    def _undo_last(self) -> None:
        with Database(self._db_path) as db:
            restored = actions.undo_last(db)
        if restored:
            self._status.setText(f"되돌리기: {restored}장을 원위치로 복구")
        else:
            self._status.setText("되돌릴 격리 작업이 없습니다 (휴지통은 OS에서 복구)")
        self.load_results()

    def _on_zoom(self, px: int) -> None:
        for grid in (self._dup_grid, self._sim_grid, self._cat_grid):
            grid.set_thumb_size(px)

    def _on_category_changed(self, text: str) -> None:
        self._load_category(None if text.startswith("전체") else text.split(" ")[0])

    # ---------- 워커 콜백 ----------
    def _on_progress(self, msg: str) -> None:
        self._status.setText(msg)
        self.statusBar().showMessage(msg)

    def _on_finished(self, summary: dict) -> None:
        self._progress.setVisible(False)
        self._start_btn.setEnabled(True)
        self._reset_btn.setEnabled(True)
        deleted = summary.get("scanned_deleted")
        deleted_text = f"{deleted:,}" if deleted is not None else "-"
        self._status.setText(
            f"완료 — 신규 {summary['scanned_new']:,} · "
            f"변경 {summary['scanned_updated']:,} · 삭제 {deleted_text} · "
            f"중복그룹 {summary['duplicate_groups']} · "
            f"유사그룹 {summary['similar_groups']} · 오류 {summary['analyzed_err']}"
        )
        self.load_results()

    def _on_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._start_btn.setEnabled(True)
        self._reset_btn.setEnabled(True)
        self._status.setText(f"오류: {msg}")

    # ---------- DB → 뷰 ----------
    def load_results(self) -> None:
        if not os.path.exists(self._db_path):
            self._dup_grid.set_groups([])
            self._sim_grid.set_groups([])
            self._refresh_category_combo({})
            return
        with Database(self._db_path) as db:
            self._dup_grid.set_groups(self._dup_groups(db))
            self._sim_grid.set_groups(self._sim_groups(db))
            counts = db.category_counts()
        self._refresh_category_combo(counts)

    @staticmethod
    def _dup_groups(db: Database) -> list[dict]:
        """완전 중복을 그룹 단위로 묶는다."""
        groups: dict[int, dict] = {}
        for r in db.iter_duplicate_groups():
            g = groups.setdefault(r["group_id"], {
                "group_id": r["group_id"], "title": "", "items": [],
            })
            rep = "★ 대표" if r["is_representative"] else ""
            g["items"].append({
                "label": f"{rep}\n{os.path.basename(r['path'])}",
                "thumb_path": r["thumb_path"], "path": r["path"],
                "file_id": r["file_id"], "keep": bool(r["is_representative"]),
                "tooltip": f"{r['path']}\n(더블클릭: 원본 열기)",
                "size": r["size"], "category": r["category"],
                "confidence": r["category_confidence"],
            })
        out = []
        for gid, g in groups.items():
            g["title"] = f"완전 중복 그룹 {gid} · {len(g['items'])}장"
            out.append(g)
        return out

    @staticmethod
    def _sim_groups(db: Database) -> list[dict]:
        """유사 사진을 그룹 단위로 묶는다(그룹당 한 줄).

        완전 중복의 '비대표' 멤버는 대표 한 장으로 접어(제외) 완전 중복이 유사
        탭에 중복 노출되지 않게 한다. 그 결과 원소가 1장뿐인 그룹(= 순수 완전
        중복 그룹)은 유사 탭에서 사라진다.
        """
        collapsed = db.nonrepresentative_duplicate_ids()
        groups: dict[int, dict] = {}
        for r in db.iter_similar_groups():
            if r["file_id"] in collapsed:
                continue  # 완전 중복의 비대표 → 접기
            g = groups.setdefault(r["group_id"], {
                "group_id": r["group_id"], "title": "", "items": [],
            })
            star = "⭐" if r["is_best_shot"] else ""
            quality, reason = None, ""
            if r["quality_detail"]:
                try:
                    quality = json.loads(r["quality_detail"])
                    reason = quality.get("reason", "")
                except (ValueError, TypeError):
                    quality = None
            g["items"].append({
                "label": f"{star} {os.path.basename(r['path'])}",
                "thumb_path": r["thumb_path"], "path": r["path"],
                "file_id": r["file_id"], "keep": bool(r["is_best_shot"]),
                "tooltip": f"{r['path']}\n유사도 {r['similarity_score']:.3f}\n{reason}"
                           f"\n(더블클릭: 원본 열기)",
                "size": r["size"], "category": r["category"],
                "confidence": r["category_confidence"],
                "similarity": r["similarity_score"],
                "is_best": bool(r["is_best_shot"]),
                "reason": reason, "quality": quality,
            })
        out = []
        for gid, g in groups.items():
            if len(g["items"]) < 2:
                continue  # 접기 후 1장뿐이면 유사 그룹이 아님(순수 완전 중복)
            g["title"] = f"유사 그룹 {gid} · {len(g['items'])}장"
            out.append(g)
        return out

    def _refresh_category_combo(self, counts: dict[str, int]) -> None:
        self._cat_combo.blockSignals(True)
        self._cat_combo.clear()
        total = sum(counts.values())
        self._cat_combo.addItem(f"전체 ({total:,})")
        for cat, n in counts.items():
            self._cat_combo.addItem(f"{cat} ({n:,})")
        self._cat_combo.blockSignals(False)
        self._load_category(None)

    def _load_category(self, category: str | None) -> None:
        if not os.path.exists(self._db_path):
            self._cat_grid.set_items([])
            return
        items = []
        with Database(self._db_path) as db:
            for r in db.iter_files_by_category(category):
                name = os.path.basename(r["path"])
                items.append({
                    "label": f"[{r['category']}]\n{name}",
                    "thumb_path": r["thumb_path"], "path": r["path"],
                    "file_id": r["file_id"],
                    "tooltip": f"{r['path']}\n(더블클릭: 원본 열기)",
                    "size": r["size"], "category": r["category"],
                    "confidence": r["category_confidence"],
                })
        self._cat_grid.set_items(items)
