"""이미지 로더 테스트 (SPEC 6.1: EXIF 회전, 손상 파일 graceful skip)."""
import io

from PIL import Image

from photo_organizer.core.image_loader import (
    SUPPORTED_EXTS,
    is_supported,
    load_normalized,
)


def test_corrupt_file_returns_none(tmp_path):
    """잘린/손상 파일은 예외 없이 None을 반환한다."""
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"\xff\xd8\xff\xe0garbage-not-a-real-jpeg")
    assert load_normalized(bad) is None


def test_unreadable_extension_returns_none(tmp_path):
    """이미지가 아닌 내용은 None."""
    f = tmp_path / "note.png"
    f.write_bytes(b"this is plain text, not a png")
    assert load_normalized(f) is None


def test_missing_file_returns_none(tmp_path):
    assert load_normalized(tmp_path / "does-not-exist.jpg") is None


def test_valid_image_loads_rgb(tmp_path):
    """정상 이미지는 RGB로 로드된다."""
    p = tmp_path / "ok.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(p)
    img = load_normalized(p)
    assert img is not None
    assert img.mode == "RGB"
    assert img.size == (20, 10)


def test_exif_orientation_normalized(tmp_path):
    """EXIF Orientation=6(90° 회전)이 반영되어 가로/세로가 바뀐다.

    원본은 가로(40x20). Orientation 6은 시계방향 90° 회전을 의미하므로,
    정규화 후에는 세로(20x40)가 되어야 한다.
    """
    p = tmp_path / "rotated.jpg"
    base = Image.new("RGB", (40, 20), (0, 128, 255))
    exif = base.getexif()
    exif[0x0112] = 6  # 0x0112 = Orientation, 6 = Rotate 90 CW
    base.save(p, exif=exif)

    # 정규화 전(raw)의 크기는 40x20.
    assert Image.open(p).size == (40, 20)
    # 정규화 후에는 회전이 반영되어 20x40.
    img = load_normalized(p)
    assert img is not None
    assert img.size == (20, 40)


def test_orientation_consistency_same_pixels(tmp_path):
    """회전 메타데이터가 있는 이미지와, 실제로 회전시켜 저장한 이미지는
    정규화 후 동일한 크기가 되어 해시 일관성의 토대가 된다."""
    a = tmp_path / "with_exif.jpg"
    base = Image.new("RGB", (30, 10), (10, 20, 30))
    exif = base.getexif()
    exif[0x0112] = 6
    base.save(a, exif=exif)

    b = tmp_path / "already_rotated.jpg"
    base.rotate(-90, expand=True).save(b)  # 실제 픽셀을 90° CW 회전

    ia, ib = load_normalized(a), load_normalized(b)
    assert ia is not None and ib is not None
    assert ia.size == ib.size == (10, 30)


def test_is_supported():
    assert is_supported("x.JPG")  # 대소문자 무시
    assert is_supported("x.heic")
    assert not is_supported("x.txt")
    assert ".jpg" in SUPPORTED_EXTS and ".heic" in SUPPORTED_EXTS
