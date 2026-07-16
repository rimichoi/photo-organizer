"""키보드 컬링 테스트: X/Delete=모달 없는 격리(cull_requested), Enter=열기(내장 네비 유지).

포커스/show 없이 keyPressEvent를 직접 호출해 시그널을 검증(가장 견고)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from photo_organizer.gui.thumbnail_grid import ThumbnailGrid

_app = QApplication.instance() or QApplication([])


def _key(k):
    return QKeyEvent(QKeyEvent.KeyPress, k, Qt.NoModifier)


def test_x_key_emits_cull_for_current_item():
    grid = ThumbnailGrid()
    grid.set_items([{"file_id": 7, "path": "/r/a.jpg", "label": "a"}])
    grid.setCurrentIndex(grid.model().index(0, 0))
    got = []
    grid.cull_requested.connect(lambda ids: got.append(ids))
    grid.keyPressEvent(_key(Qt.Key_X))
    assert got == [[7]]


def test_delete_key_emits_cull():
    grid = ThumbnailGrid()
    grid.set_items([{"file_id": 3, "path": "/r/b.jpg", "label": "b"}])
    grid.setCurrentIndex(grid.model().index(0, 0))
    got = []
    grid.cull_requested.connect(lambda ids: got.append(ids))
    grid.keyPressEvent(_key(Qt.Key_Delete))
    assert got == [[3]]


def test_no_cull_when_item_has_no_file_id():
    grid = ThumbnailGrid()
    grid.set_items([{"path": "/r/c.jpg", "label": "c"}])  # file_id 없음
    grid.setCurrentIndex(grid.model().index(0, 0))
    got = []
    grid.cull_requested.connect(lambda ids: got.append(ids))
    grid.keyPressEvent(_key(Qt.Key_X))
    assert got == []


def test_grouped_grid_forwards_cull():
    from photo_organizer.gui.thumbnail_grid import GroupedGrid
    gg = GroupedGrid()
    gg.set_groups([{"title": "g1", "group_id": 1,
                    "items": [{"file_id": 9, "path": "/r/d.jpg", "label": "d", "keep": True}]}])
    got = []
    gg.cull_requested.connect(lambda ids: got.append(ids))
    child = gg._grids[0]
    child.setCurrentIndex(child.model().index(0, 0))
    child.keyPressEvent(_key(Qt.Key_X))
    assert got == [[9]]


def test_enter_key_opens_current_item(tmp_path, monkeypatch):
    """Enter는 내장 네비를 깨지 않고 원본 열기를 호출한다."""
    from photo_organizer.gui import thumbnail_grid as tg

    f = tmp_path / "e.png"
    f.write_bytes(b"fake")

    grid = ThumbnailGrid()
    grid.set_items([{"file_id": 1, "path": str(f), "label": "e"}])
    grid.setCurrentIndex(grid.model().index(0, 0))

    opened = {}
    monkeypatch.setattr(tg.QDesktopServices, "openUrl",
                        lambda url: opened.setdefault("path", url.toLocalFile()))
    grid.keyPressEvent(_key(Qt.Key_Return))
    assert opened["path"] == str(f)


def test_backspace_key_emits_cull():
    """Backspace도 X/Delete와 동일하게 컬링을 요청한다(브리프 keyPressEvent 조건에 포함)."""
    grid = ThumbnailGrid()
    grid.set_items([{"file_id": 5, "path": "/r/e.jpg", "label": "e"}])
    grid.setCurrentIndex(grid.model().index(0, 0))
    got = []
    grid.cull_requested.connect(lambda ids: got.append(ids))
    grid.keyPressEvent(_key(Qt.Key_Backspace))
    assert got == [[5]]
