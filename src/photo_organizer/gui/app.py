"""GUI 진입점 (docs/SPEC.md Phase 4).

실행:
    photo-organizer-gui                 # 기본 DB(photo_organizer.db) 사용
    photo-organizer-gui --db lib.db --thumb-dir thumbs

multiprocessing(analyze)이 spawn 방식으로 재실행될 때를 대비해 진입점을
``if __name__ == "__main__"`` 가드 아래에 둔다.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="photo-organizer-gui")
    parser.add_argument("--db", default="photo_organizer.db", help="SQLite DB 경로")
    parser.add_argument("--thumb-dir", default="thumbnails", help="썸네일 캐시 폴더")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv[:1])
    window = MainWindow(db_path=args.db, thumb_dir=args.thumb_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
