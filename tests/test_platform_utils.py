"""플랫폼 유틸 테스트 — 긴 경로 정규화, 시스템 폴더 스킵 규칙."""
from photo_organizer.core import platform_utils as pu


def test_normalize_short_path_unchanged():
    """짧은 경로는 어느 플랫폼에서도 원본 그대로."""
    p = "/Volumes/NAS/photos/a.jpg"
    assert pu.normalize_long_path(p) == p


def test_normalize_non_windows_never_prefixes(monkeypatch):
    """non-Windows에서는 아무리 긴 경로도 접두어를 붙이지 않는다."""
    monkeypatch.setattr(pu, "IS_WINDOWS", False)
    long_path = "/Volumes/NAS/" + ("x" * 400) + "/a.jpg"
    assert pu.normalize_long_path(long_path) == long_path


def test_normalize_windows_long_path_prefixed(monkeypatch):
    r"""Windows에서 260자 초과 로컬 경로에 \\?\ 접두어가 붙는다."""
    monkeypatch.setattr(pu, "IS_WINDOWS", True)
    long_path = "C:\\photos\\" + ("d" * 300) + "\\a.jpg"
    out = pu.normalize_long_path(long_path)
    assert out.startswith("\\\\?\\")
    assert not out.startswith("\\\\?\\UNC")


def test_normalize_windows_long_unc_prefixed(monkeypatch):
    r"""Windows UNC 긴 경로는 \\?\UNC\ 접두어를 받는다."""
    monkeypatch.setattr(pu, "IS_WINDOWS", True)
    unc = "\\\\server\\share\\" + ("d" * 300) + "\\a.jpg"
    out = pu.normalize_long_path(unc)
    assert out.startswith("\\\\?\\UNC\\server\\share")


def test_should_skip_dir_macos(monkeypatch):
    monkeypatch.setattr(pu, "IS_MACOS", True)
    monkeypatch.setattr(pu, "IS_WINDOWS", False)
    assert pu.should_skip_dir(".Spotlight-V100")
    assert pu.should_skip_dir(".Trashes")
    assert not pu.should_skip_dir("Vacation2024")


def test_should_skip_dir_windows(monkeypatch):
    monkeypatch.setattr(pu, "IS_WINDOWS", True)
    monkeypatch.setattr(pu, "IS_MACOS", False)
    assert pu.should_skip_dir("$RECYCLE.BIN")
    assert pu.should_skip_dir("System Volume Information")
    assert not pu.should_skip_dir("Photos")
