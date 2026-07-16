"""선택 항목 상세 패널.

그리드에서 선택한 사진의 큰 미리보기와 상세 정보를 보여준다:
파일명·경로·크기·분류(신뢰도)·유사도·베스트샷 근거/품질 지표, 그리고
원본에서 읽은 EXIF(촬영기기·촬영일·해상도).
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..core.image_loader import read_exif_summary

_PREVIEW_PX = 280


def _human_size(n: int | None) -> str:
    if not n:
        return "-"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class DetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(300)
        lay = QVBoxLayout(self)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setMinimumHeight(_PREVIEW_PX)
        self._preview.setFrameShape(QFrame.StyledPanel)
        lay.addWidget(self._preview)

        self._title = QLabel("항목을 선택하세요")
        self._title.setWordWrap(True)
        f = self._title.font(); f.setBold(True); self._title.setFont(f)
        lay.addWidget(self._title)

        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._info.setAlignment(Qt.AlignTop)
        lay.addWidget(self._info, 1)

        self.clear()

    def clear(self) -> None:
        self._preview.setPixmap(QPixmap())
        self._preview.setText("(미리보기)")
        self._title.setText("항목을 선택하세요")
        self._info.setText("")

    def show_item(self, item: dict) -> None:
        path = item.get("path")
        # 미리보기 (썸네일 사용 → 빠르고 네트워크 재접근 없음)
        thumb = item.get("thumb_path")
        pm = QPixmap(thumb) if thumb and os.path.exists(thumb) else QPixmap()
        if not pm.isNull():
            self._preview.setPixmap(
                pm.scaled(_PREVIEW_PX, _PREVIEW_PX, Qt.KeepAspectRatio,
                          Qt.SmoothTransformation)
            )
            self._preview.setText("")
        else:
            self._preview.setPixmap(QPixmap())
            self._preview.setText("(미리보기 없음)")

        name = os.path.basename(path) if path else item.get("label", "")
        self._title.setText(name)
        self._info.setText(self._format_info(item, path))

    @staticmethod
    def _format_info(item: dict, path: str | None) -> str:
        rows: list[str] = []

        def add(k, v):
            if v not in (None, "", "-"):
                rows.append(f"<b>{k}</b>: {v}")

        if item.get("is_best"):
            rows.append("⭐ <b>베스트샷</b>")
        add("분류", _with_conf(item.get("category"), item.get("confidence")))
        if item.get("similarity") is not None:
            add("유사도", f"{item['similarity']:.3f}")
        add("베스트샷 근거", item.get("reason"))

        q = item.get("quality")
        if q:
            add("선명도", q.get("sharpness"))
            add("노출", q.get("exposure"))
            add("대비", q.get("contrast"))
            if q.get("eyes_open") is not None:
                add("눈 뜸 비율", q.get("eyes_open"))

        add("파일 크기", _human_size(item.get("size")))

        # EXIF (원본에서 읽음)
        if path and os.path.exists(path):
            ex = read_exif_summary(path)
            if ex["width"] and ex["height"]:
                add("해상도", f"{ex['width']} × {ex['height']}")
            cam = " ".join(x for x in (ex.get("make"), ex.get("model")) if x)
            add("촬영기기", cam)
            add("촬영일", ex.get("datetime"))

        add("경로", path)
        return "<br>".join(rows)


def _with_conf(cat, conf) -> str | None:
    if not cat:
        return None
    return f"{cat} (신뢰도 {conf:.2f})" if conf is not None else cat
