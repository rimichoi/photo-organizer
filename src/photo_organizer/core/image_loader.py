"""이미지 로더 — EXIF Orientation 정규화, HEIC/RAW 대응, 손상 파일 graceful skip.

docs/SPEC.md 3.3 참조:
- EXIF Orientation을 반영하지 않으면 회전된 같은 사진이 다르게 인식되므로,
  로드 시 항상 정규화한다.
- HEIC(iPhone), RAW(카메라)는 기본 Pillow로 안 열릴 수 있어 별도 처리한다.
- 잘린 JPEG 등 손상 파일은 예외를 삼켜 ``None``을 반환한다(전체 작업 중단 금지).

이 모듈은 유사도 해시(Phase 2)와 분류(Phase 2~3)의 공통 입력을 만든다.
완전 중복(byte SHA-256)은 이미지를 디코딩하지 않으므로 이 로더를 쓰지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from .platform_utils import normalize_long_path

# HEIC/HEIF 지원은 pillow-heif가 있을 때만 등록한다.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover - 환경 의존
    HEIF_AVAILABLE = False

# RAW 지원은 rawpy가 있을 때만.
try:
    import rawpy

    RAW_AVAILABLE = True
except ImportError:  # pragma: no cover - 환경 의존
    RAW_AVAILABLE = False

# Pillow(+pillow-heif)로 여는 일반 래스터/HEIF 포맷.
RASTER_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".tif", ".tiff", ".webp",
})
HEIF_EXTS = frozenset({".heic", ".heif"})
# 카메라 RAW (rawpy가 있을 때만 실제 디코딩).
RAW_EXTS = frozenset({
    ".cr2", ".cr3", ".nef", ".arw", ".dng",
    ".raf", ".orf", ".rw2", ".pef", ".srw",
})

# 스캐너가 후보 파일을 거를 때 공유하는 전체 지원 확장자 집합.
SUPPORTED_EXTS = RASTER_EXTS | HEIF_EXTS | RAW_EXTS


def parse_exif_datetime(s: str | None) -> float | None:
    """EXIF 'YYYY:MM:DD HH:MM:SS' 문자열을 epoch초(float)로. 실패 시 None."""
    if not s or not isinstance(s, str):
        return None
    from datetime import datetime
    try:
        return datetime.strptime(s.strip(), "%Y:%m:%d %H:%M:%S").timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _load_raw(norm_path: str) -> Image.Image:
    """rawpy로 RAW를 디코딩해 RGB PIL 이미지로 변환한다.

    rawpy의 postprocess는 촬영 시 회전 플래그를 기본 반영하므로 별도 EXIF
    transpose가 필요 없다.
    """
    with rawpy.imread(norm_path) as raw:
        rgb = raw.postprocess()
    return Image.fromarray(rgb)


def load_normalized(path: str | Path) -> Optional[Image.Image]:
    """정규화된 RGB 이미지를 반환한다. 열 수 없으면 ``None``.

    - EXIF Orientation을 반영해 실제 표시 방향으로 회전(``exif_transpose``).
    - 손상/미지원/권한 오류는 예외를 삼켜 ``None`` 반환(호출측이 "손상"으로
      기록하고 계속 진행하도록).
    """
    ext = Path(str(path)).suffix.lower()
    norm = normalize_long_path(str(path))
    try:
        if ext in RAW_EXTS:
            if not RAW_AVAILABLE:
                return None
            img = _load_raw(norm)
        else:
            img = Image.open(norm)
            # EXIF Orientation 반영 (메타데이터 없으면 원본 그대로 반환).
            img = ImageOps.exif_transpose(img)
        return img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        # UnidentifiedImageError: 미지원/헤더 손상
        # OSError: 잘린 파일·권한·네트워크 끊김
        # ValueError/SyntaxError: 일부 손상 케이스에서 Pillow가 던짐
        return None


def load_analyzed(path: str | Path) -> tuple[Optional[Image.Image], dict]:
    """정규화 RGB 이미지와 메타데이터를 한 번의 파일 열기로 함께 반환한다.

    네트워크 재접근을 피하기 위해(SPEC 5.1) 분류·해시·썸네일이 공유하는 진입점.
    반환 meta = {"make": 제조사|None, "model": 모델|None, "dt": 촬영시각(epoch초)|None}.
    카메라 정보 유무는 스크린샷 판정(SPEC 4.4)에 쓰이고, "dt"는 버스트 그룹핑
    (classify/similar.py)에 쓰인다. 실패 시 (None, {}).
    """
    ext = Path(str(path)).suffix.lower()
    norm = normalize_long_path(str(path))
    try:
        if ext in RAW_EXTS:
            if not RAW_AVAILABLE:
                return None, {}
            # RAW는 정의상 카메라 촬영본 → 스크린샷 판정에서 카메라 있음으로 취급.
            return _load_raw(norm).convert("RGB"), {"make": "RAW", "model": None}
        img = Image.open(norm)
        exif = img.getexif()
        # 0x010F=Make, 0x0110=Model
        dt_raw = exif.get(0x0132)  # DateTime
        if not dt_raw:
            try:
                dt_raw = exif.get_ifd(0x8769).get(0x9003)  # DateTimeOriginal
            except Exception:
                dt_raw = None
        meta = {"make": exif.get(0x010F), "model": exif.get(0x0110),
                "dt": parse_exif_datetime(dt_raw)}
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB"), meta
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None, {}


def read_exif_summary(path: str | Path) -> dict:
    """상세 패널 표시용 EXIF/해상도 요약. 실패 시 값들은 None.

    반환: {"width", "height", "make", "model", "datetime"}
    RAW/미지원/손상 파일은 조용히 빈 값으로 둔다.
    """
    info: dict = {
        "width": None, "height": None,
        "make": None, "model": None, "datetime": None,
    }
    ext = Path(str(path)).suffix.lower()
    if ext in RAW_EXTS:
        return info  # RAW는 여기서 디코딩하지 않음
    try:
        with Image.open(normalize_long_path(str(path))) as img:
            info["width"], info["height"] = img.size
            exif = img.getexif()
            info["make"] = exif.get(0x010F)
            info["model"] = exif.get(0x0110)
            dt = exif.get(0x0132)  # DateTime
            if not dt:
                try:
                    dt = exif.get_ifd(0x8769).get(0x9003)  # DateTimeOriginal
                except Exception:
                    dt = None
            info["datetime"] = dt
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        pass
    return info


def is_supported(path: str | Path) -> bool:
    """확장자만으로 지원 대상 이미지인지 (빠른 필터). 실제 로드는 하지 않는다."""
    return Path(str(path)).suffix.lower() in SUPPORTED_EXTS
