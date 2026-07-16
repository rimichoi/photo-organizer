# 패키징 (PyInstaller) — Photo Organizer

Windows `.exe` / macOS `.app` 실행 파일 빌드 안내. spec은 `photo_organizer.spec`,
진입점은 `packaging/pyinstaller_entry.py`.

> PyInstaller는 **크로스컴파일 불가** — Windows 빌드는 Windows에서, macOS 빌드는
> macOS에서 각각 수행해야 한다.

## 사전 준비

```bash
# 의존성(코어 + GUI) + 빌드 도구
pip install -e ".[gui]"
pip install "pyinstaller>=6.0"      # 또는 pip install -e ".[build]"
```

검증 환경(현재): macOS, Python 3.14, PySide6 6.11, **PyInstaller 6.21** — 3.14에서 정상.
`onnxruntime`은 3.14 wheel이 없고 AI가 보류 상태라 spec에서 **제외**(`excludes`)한다.
AI 단계를 켤 때는 Python 3.12 환경에서 `onnxruntime`을 넣고 `excludes`에서 제거할 것.

## 빌드

```bash
PYTHONPATH=src pyinstaller photo_organizer.spec --noconfirm --clean
```

산출물(`dist/`):
- **macOS**: `dist/PhotoOrganizer.app` (+ `dist/PhotoOrganizer/` onedir)
- **Windows/Linux**: `dist/PhotoOrganizer/PhotoOrganizer(.exe)` (onedir)

`onefile` 대신 **onedir**를 쓴다: 시작이 빠르고, 대형 Qt/네이티브 의존성에서
백신 오탐이 적다. `UPX`는 오탐/서명 문제를 유발하므로 끈다(`upx=False`).

## 스모크 테스트

빌드 직후 지연 import 의존성(imagehash·rawpy·pillow_heif·cv2·send2trash)까지
frozen 번들에서 실제로 불러오는지 확인:

```bash
# macOS
dist/PhotoOrganizer.app/Contents/MacOS/PhotoOrganizer --selftest   # → SELFTEST OK
# Windows
dist\PhotoOrganizer\PhotoOrganizer.exe --selftest
```

`SELFTEST OK`가 나오면 번들이 완결된 것. GUI 기동만 확인하려면 인자 없이 실행.

## macOS 서명 & 공증 (배포 시 필수)

미서명 앱은 다른 Mac에서 Gatekeeper가 차단한다(로컬은 우클릭→열기로 우회 가능).
배포하려면 Developer ID로 서명 + 공증(notarization):

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: 이름 (팀ID)" dist/PhotoOrganizer.app
# 공증
ditto -c -k --keepParent dist/PhotoOrganizer.app PhotoOrganizer.zip
xcrun notarytool submit PhotoOrganizer.zip --apple-id <id> --team-id <team> \
  --password <app-specific-pw> --wait
xcrun stapler staple dist/PhotoOrganizer.app
```

> 함정: PyInstaller가 빌드 중 ad-hoc 서명을 하므로, Developer ID 재서명은
> `--deep --force`로 덮어써야 한다. `--options runtime`(hardened runtime)이 공증 요건.

## Windows 서명 & SmartScreen

- PyInstaller 부트로더는 백신 **오탐**이 잦다 → 코드 서명으로 완화:
  ```bat
  signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 ^
    dist\PhotoOrganizer\PhotoOrganizer.exe
  ```
- **SmartScreen**은 EV 인증서라도 즉시 평판을 주지 않고 다운로드가 누적돼야 함.
  MSIX 패키징/Microsoft Store 경로로 우회 가능.

## 자동 업데이트(선택, 향후)

macOS는 Sparkle, Windows는 Squirrel, 파이썬 공통은 PyUpdater 검토.

## 참고
- `dist/`, `build/`는 `.gitignore` 처리(커밋 금지). spec/진입 스크립트만 버전관리.
- 실행 시 DB(`photo_organizer.db`)·썸네일(`thumbnails/`)은 **실행 위치 기준**으로
  생성된다. 배포 시 쓰기 가능한 사용자 폴더로 경로를 옮기는 것이 바람직(후속 개선).
