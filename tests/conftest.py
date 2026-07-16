"""pytest 공통 설정 — src 레이아웃을 import 경로에 추가한다."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
