"""촬영 날짜 결정 테스트 (spec §3.1): EXIF 우선 → 파일명 → 미상."""
from datetime import date, datetime, timedelta

import pytest

from photo_organizer.core.date_source import (
    SOURCE_EXIF,
    SOURCE_FILENAME,
    SOURCE_UNKNOWN,
    date_from_filename,
    resolve_date,
)


def _epoch(y: int, m: int, d: int) -> float:
    return datetime(y, m, d, 12, 0, 0).timestamp()


def test_exif_wins_over_filename():
    # exif_dt가 있으면 파일명 날짜(2020-01-01)는 무시한다.
    got, source = resolve_date(_epoch(2023, 4, 12), "IMG_20200101_090000.jpg")
    assert got == date(2023, 4, 12)
    assert source == SOURCE_EXIF


@pytest.mark.parametrize(
    "name",
    [
        "IMG_20230412_183000.jpg",       # 안드로이드/카메라
        "VID_20230412_183000.mp4",
        "PXL_20230412_183000123.jpg",    # 픽셀
        "KakaoTalk_20230412_183000.jpg", # 카카오톡
        "IMG-20230412-WA0001.jpg",       # 왓츠앱
        "Screenshot 2023-04-12 at 18.30.00.png",  # macOS/iOS 스크린샷
        "2023-04-12 18.30.00.jpg",
        "20230412_183000.jpg",
        "2023_04_12.jpg",
        "2023.04.12.jpg",
    ],
)
def test_filename_patterns(name):
    assert date_from_filename(name) == date(2023, 4, 12)


def test_filename_used_when_no_exif():
    got, source = resolve_date(None, "IMG_20230412_183000.jpg")
    assert got == date(2023, 4, 12)
    assert source == SOURCE_FILENAME


@pytest.mark.parametrize(
    "name",
    [
        "12345678901234.jpg",   # 숫자 경계 — 긴 일련번호 안의 8자리는 무시
        "20231345_1200.jpg",    # 13월 — 유효성 탈락
        "20230230_1200.jpg",    # 2월 30일 — 유효성 탈락
        "18990101_1200.jpg",    # 범위 밖(1990 이전)
        "DSC_0001.jpg",         # 날짜 없음
        "2023-0412.jpg",        # 구분자 불일치
    ],
)
def test_no_false_positives(name):
    assert date_from_filename(name) is None


def test_future_date_rejected():
    future = date.today() + timedelta(days=30)
    assert date_from_filename(f"IMG_{future:%Y%m%d}_120000.jpg") is None


def test_unknown_when_nothing_available():
    got, source = resolve_date(None, "DSC_0001.jpg")
    assert got is None
    assert source == SOURCE_UNKNOWN


def test_first_valid_match_wins():
    # 앞쪽 후보가 무효(13월)면 뒤쪽 유효 후보를 쓴다.
    assert date_from_filename("20231345_20230412.jpg") == date(2023, 4, 12)


@pytest.mark.parametrize(
    "exif_dt",
    [0.0, 4102444800.0, -2208988800.0],  # 1970 / 2100 / 1900
)
def test_out_of_range_exif_falls_back_to_filename(exif_dt):
    # 손상된 EXIF가 파일명의 멀쩡한 날짜를 이기면 안 된다.
    got, source = resolve_date(exif_dt, "IMG_20230412_101112.jpg")
    assert got == date(2023, 4, 12)
    assert source == SOURCE_FILENAME


def test_out_of_range_exif_without_filename_date_is_unknown():
    got, source = resolve_date(0.0, "DSC_0001.jpg")
    assert got is None
    assert source == SOURCE_UNKNOWN
