"""촬영 날짜 결정 — EXIF 우선, 없으면 파일명, 그것도 없으면 미상.

설계: docs/superpowers/specs/2026-08-06-date-organize-design.md §3.1

파일을 열지 않는 순수 함수만 둔다. EXIF 읽기는 analyze 단계가 이미 끝냈고
(`files.exif_dt`), 여기서는 그 값과 파일명만 보고 날짜를 정한다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

SOURCE_EXIF = "exif"
SOURCE_FILENAME = "filename"
SOURCE_UNKNOWN = "unknown"

# YYYY[구분자]MM[구분자]DD. 구분자는 없음/-/_/. 를 허용하되 앞뒤가 같아야 한다
# (`2023-0412` 같은 혼합 형태 차단). 앞뒤 숫자 경계를 막아 긴 일련번호 안의
# 8자리가 날짜로 둔갑하지 않게 한다. 월/일의 실제 유효성은 date()가 검증한다.
_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})([-_.]?)(\d{2})\2(\d{2})(?!\d)")

# 디지털 사진 이전 연도나 epoch 숫자·해상도 문자열을 걸러내는 하한.
_MIN_DATE = date(1990, 1, 1)


def _in_range(candidate: date) -> bool:
    """1990-01-01 ~ 오늘+1일 안에 드는 날짜인지."""
    return _MIN_DATE <= candidate <= date.today() + timedelta(days=1)


def date_from_filename(name: str) -> date | None:
    """파일명에서 촬영 날짜를 추정한다. 찾지 못하면 ``None``.

    확장자를 뗀 stem만 검사하며, 유효하고 범위 안에 드는 **첫 매치**를 쓴다.
    """
    stem = Path(name).stem
    for m in _DATE_RE.finditer(stem):
        try:
            candidate = date(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            continue  # 13월·2월 30일 등
        if _in_range(candidate):
            return candidate
    return None


def resolve_date(exif_dt: float | None, filename: str) -> tuple[date | None, str]:
    """(날짜, 출처)를 반환한다. 출처는 exif/filename/unknown.

    ``exif_dt``는 analyze가 기록한 epoch초다. parse_exif_datetime이 EXIF의
    로컬 시간 문자열을 로컬 타임존 기준으로 epoch화했으므로, 로컬 시간으로
    되돌리면 원래 촬영 시각이 그대로 복원된다.

    EXIF에도 파일명과 같은 범위 검증을 적용한다. 손상된 EXIF(0=1970, 미래,
    음수 epoch)를 그대로 믿으면 엉뚱한 년월 폴더가 생기고, 음수 epoch은
    플랫폼마다 동작이 달라 결과가 갈린다. 범위를 벗어나면 파일명으로 폴백한다.
    """
    if exif_dt is not None:
        try:
            candidate = datetime.fromtimestamp(exif_dt).date()
        except (OverflowError, OSError, ValueError):
            candidate = None  # 손상된 값 → 파일명으로 폴백
        if candidate is not None and _in_range(candidate):
            return candidate, SOURCE_EXIF
    from_name = date_from_filename(filename)
    if from_name is not None:
        return from_name, SOURCE_FILENAME
    return None, SOURCE_UNKNOWN
