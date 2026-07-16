r"""플랫폼(Windows/macOS/Linux) 의존적인 경로·파일 처리를 한 곳에 격리한다.

나머지 코어 모듈은 이 모듈만 통해 OS 차이를 다루므로, 플랫폼 분기가
코드 전반에 흩어지지 않는다 (docs/SPEC.md 3.1 경로/시스템 처리 참조).

- Windows: 260자(MAX_PATH) 초과 경로에 ``\\?\`` / ``\\?\UNC\`` 접두어를 붙여
  긴 경로를 지원한다.
- macOS: ``/Volumes`` 아래에 마운트되는 네트워크 드라이브와 각종 시스템
  메타데이터 폴더(``.Spotlight-V100`` 등)를 스캔에서 제외한다.
"""
from __future__ import annotations

import sys
import unicodedata

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Windows MAX_PATH. 이 길이 이상이면 \\?\ 접두어가 필요하다.
_MAX_PATH = 260

# macOS 볼륨/홈에 존재하는 시스템 메타데이터 디렉토리. 사진이 없고 접근 시
# 권한 오류를 일으키므로 스캔에서 건너뛴다.
_MACOS_SYSTEM_DIRS = frozenset({
    ".Spotlight-V100",
    ".Trashes",
    ".fseventsd",
    ".DocumentRevisions-V100",
    ".TemporaryItems",
    ".apdisk",
})

# Windows에서 스캔 가치가 없고 접근 오류가 잦은 시스템 디렉토리.
_WINDOWS_SYSTEM_DIRS = frozenset({
    "$RECYCLE.BIN",
    "System Volume Information",
})


def normalize_long_path(path: str) -> str:
    r"""OS 파일 API에 넘길 안전한 경로 문자열을 반환한다.

    Windows에서 260자를 넘으면 절대경로로 바꾼 뒤 ``\\?\`` 접두어를 붙인다
    (UNC 경로 ``\\server\share``는 ``\\?\UNC\server\share`` 형태). 그 외
    플랫폼이나 짧은 경로는 원본을 그대로 돌려준다.

    저장(DB)에는 접두어 없는 원본 경로를 쓰고, 실제 파일 접근 직전에만 이
    함수를 통과시키는 것을 원칙으로 한다.
    """
    s = str(path)
    if not IS_WINDOWS:
        return s
    if len(s) < _MAX_PATH:
        return s
    if s.startswith("\\\\?\\"):
        return s  # 이미 접두어가 있음
    if s.startswith("\\\\"):
        # UNC: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + s[2:]
    # 드라이브 절대경로(C:\...)는 그대로 접두어만, 상대경로만 절대화한다
    # (접두어는 완전 정규화 경로를 요구).
    if len(s) >= 3 and s[1] == ":" and s[2] in "\\/":
        return "\\\\?\\" + s
    import os

    return "\\\\?\\" + os.path.abspath(s)


def to_nfc(path: str) -> str:
    """경로 문자열을 유니코드 NFC로 정규화한다.

    macOS는 파일명을 NFD(자모 분리)로 다루고 Windows/대부분 NAS는 NFC(음절 결합)를
    쓴다. DB 저장·비교 경계에서 이 함수로 NFC로 통일해 한글 파일명의 중복 오탐과
    증분 재스캔/삭제 감지 오판을 막는다. (파일 API 접근용 접두어 처리는
    normalize_long_path가 별도로 담당 — 역할이 다르다.)
    """
    return unicodedata.normalize("NFC", str(path))


def should_skip_dir(name: str) -> bool:
    """스캔에서 통째로 건너뛸 시스템 디렉토리 이름인지 판정한다."""
    if IS_MACOS and name in _MACOS_SYSTEM_DIRS:
        return True
    if IS_WINDOWS and name in _WINDOWS_SYSTEM_DIRS:
        return True
    return False
