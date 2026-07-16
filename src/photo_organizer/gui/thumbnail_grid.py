"""가상 스크롤 썸네일 그리드 (docs/SPEC.md 3.5, FR-06) + 편의 기능.

- 지연 로딩: 보이는 항목만 썸네일 로드(가상 스크롤) + LRU 캐시 → 10만 장 대응
- 더블클릭: OS 기본 뷰어로 원본 열기
- 우클릭 메뉴: 원본 열기 / 폴더에서 보기 / 경로 복사
- 그룹 구분: group_id 홀짝으로 은은한 배경 틴트
- 빈 화면 안내 문구, 썸네일 크기 조절, 선택 시그널(상세 패널 연동)
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListView, QMenu, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

_DEFAULT_PX = 160
_CACHE_MAX = 2000
_GROUP_TINT = QColor(128, 128, 128, 28)  # 반투명 → 라이트/다크 모두 은은


class ThumbnailModel(QAbstractListModel):
    """항목 리스트(dict)를 그리드로 노출한다.

    item 키: label, thumb_path, path, tooltip, group_id(선택), 그리고 상세 패널용
    부가 정보(size, category, similarity, is_best, reason, quality 등).
    """

    def __init__(self, items: list[dict] | None = None, thumb_px: int = _DEFAULT_PX):
        super().__init__()
        self._items: list[dict] = items or []
        self._thumb_px = thumb_px
        self._cache: "OrderedDict[str, QPixmap]" = OrderedDict()

    def set_items(self, items: list[dict]) -> None:
        self.beginResetModel()
        self._items = items
        self._cache.clear()
        self.endResetModel()

    def set_thumb_px(self, px: int) -> None:
        self.beginResetModel()
        self._thumb_px = px
        self._cache.clear()   # 크기 바뀌면 캐시 무효화
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def item_at(self, row: int) -> dict | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def path_at(self, row: int) -> str | None:
        item = self.item_at(row)
        return item.get("path") if item else None

    def _pixmap(self, path: str | None) -> QPixmap:
        if not path:
            return QPixmap()
        pm = self._cache.get(path)
        if pm is not None:
            self._cache.move_to_end(path)
            return pm
        pm = QPixmap(path)
        if not pm.isNull():
            pm = pm.scaled(self._thumb_px, self._thumb_px,
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._cache[path] = pm
        if len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)
        return pm

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        item = self._items[index.row()]
        if role == Qt.DisplayRole:
            return item.get("label", "")
        if role == Qt.DecorationRole:
            return self._pixmap(item.get("thumb_path"))
        if role == Qt.ToolTipRole:
            return item.get("tooltip", "")
        if role == Qt.BackgroundRole:
            gid = item.get("group_id")
            if gid is not None and gid % 2 == 1:
                return _GROUP_TINT
        return None


class ThumbnailGrid(QListView):
    """썸네일 격자 뷰. 더블클릭/우클릭/선택 연동 포함."""

    selected = Signal(dict)              # 현재 선택 항목 dict (상세 패널이 구독)
    action_requested = Signal(str, list) # (kind, [file_id,...]) — trash/quarantine
    cull_requested = Signal(list)        # 키보드 컬링(모달 없는 격리) — [file_id,...]

    def __init__(self, placeholder: str = "", single_row: bool = False):
        super().__init__()
        self._placeholder = placeholder
        self._single_row = single_row
        self._thumb_px = _DEFAULT_PX
        self._model = ThumbnailModel(thumb_px=self._thumb_px)
        self.setModel(self._model)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setSpacing(6)
        if single_row:
            # 그룹 1개 = 가로 한 줄. 넘치면 가로 스크롤.
            self.setFlow(QListView.LeftToRight)
            self.setWrapping(False)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._apply_sizes()

        self.doubleClicked.connect(self._open_item)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.selectionModel().currentChanged.connect(self._on_current_changed)

    # ---- 크기/레이아웃 ----
    def _apply_sizes(self) -> None:
        self.setIconSize(QSize(self._thumb_px, self._thumb_px))
        self.setGridSize(QSize(self._thumb_px + 24, self._thumb_px + 44))
        if self._single_row:
            # 캡션(2줄) + 여백 포함해 딱 한 줄 높이로 고정
            self.setFixedHeight(self._thumb_px + 64)

    def set_thumb_size(self, px: int) -> None:
        self._thumb_px = px
        self._model.set_thumb_px(px)
        self._apply_sizes()

    # ---- 데이터 ----
    def set_items(self, items: list[dict]) -> None:
        self._model.set_items(items)

    def count(self) -> int:
        return self._model.rowCount()

    # ---- 상호작용 ----
    def _on_current_changed(self, current: QModelIndex, _prev: QModelIndex) -> None:
        item = self._model.item_at(current.row())
        if item:
            self.selected.emit(item)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        idx = self.selectionModel().currentIndex()
        item = self._model.item_at(idx.row()) if idx.isValid() else None
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if idx.isValid():
                self._open_item(idx)
            return
        if key in (Qt.Key_X, Qt.Key_Delete, Qt.Key_Backspace):
            fid = item.get("file_id") if item else None
            if fid is not None:
                self.cull_requested.emit([fid])
            return
        super().keyPressEvent(event)  # 화살표 등 내장 네비게이션 유지

    def _open_item(self, index: QModelIndex) -> None:
        self._open_path(self._model.path_at(index.row()))

    def _open_path(self, path: str | None) -> None:
        if not path:
            return
        if not os.path.exists(path):
            QMessageBox.warning(
                self, "파일 없음",
                f"파일을 찾을 수 없습니다.\n(이동·삭제되었을 수 있습니다)\n\n{path}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _context_menu(self, pos) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        path = self._model.path_at(index.row())
        if not path:
            return
        item = self._model.item_at(index.row())
        fid = item.get("file_id") if item else None
        menu = QMenu(self)
        menu.addAction("원본 열기", lambda: self._open_path(path))
        menu.addAction("폴더에서 보기", lambda: self._reveal_in_folder(path))
        menu.addAction("경로 복사", lambda: QApplication.clipboard().setText(path))
        if fid is not None:
            menu.addSeparator()
            menu.addAction("🗑 휴지통으로 보내기",
                           lambda: self.action_requested.emit("trash", [fid]))
            menu.addAction("격리 폴더로 이동",
                           lambda: self.action_requested.emit("quarantine", [fid]))
        menu.exec(self.viewport().mapToGlobal(pos))

    @staticmethod
    def _reveal_in_folder(path: str) -> None:
        """파일 관리자에서 해당 파일을 선택해 보여준다(OS별)."""
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", path], check=False)
            elif os.name == "nt":
                subprocess.run(["explorer", f"/select,{path}"], check=False)
            else:
                subprocess.run(["xdg-open", os.path.dirname(path)], check=False)
        except OSError:
            pass

    # ---- 빈 화면 안내 ----
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._model.rowCount() == 0 and self._placeholder:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(QPalette.Disabled, QPalette.Text))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, self._placeholder)
            painter.end()


class GroupedGrid(QScrollArea):
    """그룹을 세로로 쌓아 보여준다 — 각 그룹은 [헤더 + 그 그룹 사진 한 줄].

    줄바꿈으로 그룹 경계가 흐려지는 문제를 없앤다: 한 그룹의 사진은 항상 같은
    가로 줄에 모이고, 그룹 사이는 헤더/구분선으로 명확히 나뉜다.
    """

    selected = Signal(dict)
    action_requested = Signal(str, list)
    cull_requested = Signal(list)

    def __init__(self, placeholder: str = ""):
        super().__init__()
        self._placeholder = placeholder
        self._thumb_px = _DEFAULT_PX
        self._grids: list[ThumbnailGrid] = []
        self._total = 0

        self.setWidgetResizable(True)
        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(4, 4, 4, 4)
        self._vbox.setSpacing(4)
        self.setWidget(self._container)
        self._empty = QLabel(placeholder)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setEnabled(False)  # 테마별 흐린 색 자동 적용
        self._vbox.addWidget(self._empty)
        self._vbox.addStretch(1)

    def _clear(self) -> None:
        for g in self._grids:
            g.setParent(None)
            g.deleteLater()
        self._grids.clear()
        # 헤더/구분선 위젯도 제거 (empty 라벨과 stretch만 남김)
        for i in reversed(range(self._vbox.count())):
            w = self._vbox.itemAt(i).widget()
            if w is not None and w is not self._empty:
                w.setParent(None)
                w.deleteLater()

    def set_groups(self, groups: list[dict]) -> None:
        """groups: [{"title": str, "group_id": int, "items": [item,...]}, ...]"""
        self._clear()
        self._total = sum(len(g["items"]) for g in groups)
        self._empty.setVisible(not groups)

        # stretch 앞에 삽입하기 위해 마지막 stretch 인덱스 계산
        insert_at = self._vbox.count() - 1
        for g in groups:
            # 헤더: 제목 + '여분 정리' 버튼(보존 대상 제외한 나머지를 휴지통으로)
            header = QWidget()
            hb = QHBoxLayout(header); hb.setContentsMargins(0, 2, 0, 0)
            title = QLabel(g["title"])
            tf = title.font(); tf.setBold(True); title.setFont(tf)
            hb.addWidget(title); hb.addStretch(1)
            extras = [it["file_id"] for it in g["items"]
                      if not it.get("keep") and it.get("file_id") is not None]
            if extras:
                btn = QPushButton(f"여분 정리 ({len(extras)}장)")
                btn.setToolTip("보존 대상(대표/⭐)을 제외한 나머지를 휴지통으로 보냅니다")
                btn.clicked.connect(
                    lambda _=False, ids=extras: self.action_requested.emit("trash", ids)
                )
                hb.addWidget(btn)
            self._vbox.insertWidget(insert_at, header); insert_at += 1

            line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken)
            self._vbox.insertWidget(insert_at, line); insert_at += 1

            grid = ThumbnailGrid(single_row=True)
            grid.set_thumb_size(self._thumb_px)
            grid.set_items(g["items"])
            grid.selected.connect(self.selected)
            grid.action_requested.connect(self.action_requested)
            grid.cull_requested.connect(self.cull_requested)
            self._vbox.insertWidget(insert_at, grid); insert_at += 1
            self._grids.append(grid)

    def set_thumb_size(self, px: int) -> None:
        self._thumb_px = px
        for g in self._grids:
            g.set_thumb_size(px)

    def count(self) -> int:
        return self._total
