"""PyInstaller 진입 스크립트.

패키징된 실행 파일의 시작점. photo_organizer 패키지를 절대 import로 불러
GUI를 띄운다(gui/app.py는 상대 import라 스크립트로 직접 실행할 수 없으므로
별도 진입점을 둔다). 빌드 시 spec의 pathex=src 로 패키지를 찾는다.
"""
from __future__ import annotations

import sys


_SELFTEST_MODULES = (
    "photo_organizer.classify.analyze",
    "photo_organizer.classify.phash",
    "photo_organizer.classify.rules",
    "photo_organizer.classify.bestshot",
    "photo_organizer.core.actions",
    "photo_organizer.core.image_loader",
    # 지연 import 되는 무거운 네이티브/서드파티 의존성(패키징 누락이 잦은 것들)
    "imagehash",
    "rawpy",
    "pillow_heif",
    "cv2",
    "send2trash",
    "PIL.Image",
)


def _run() -> int:
    if "--selftest" in sys.argv:
        # 패키징 스모크: GUI 시작 경로가 안 타는 지연 import 의존성까지 실제로
        # 불러 frozen 번들의 완결성을 확인한다(GUI 없이 종료).
        import importlib
        for name in _SELFTEST_MODULES:
            importlib.import_module(name)
        print("SELFTEST OK")
        return 0
    # 패키징 환경에서 인자 없이 기본값(DB/썸네일은 실행 위치 기준)으로 GUI 시작.
    from photo_organizer.gui.app import main
    return main([])


if __name__ == "__main__":
    sys.exit(_run())
