# Photo Organizer — 프로젝트 가이드 (Claude Code용)

이 파일은 Claude Code가 프로젝트 맥락을 파악하기 위한 문서입니다. 작업 시작 전 반드시 `docs/SPEC.md`를 함께 읽으세요.

## 프로젝트 한 줄 요약
Windows에서 네트워크 드라이브의 대용량 사진(10만 장 이상)을 스캔해 **완전 중복 · 유사 사진 · 종류별 분류(스크린샷/문서/흔들린 사진) · 유사 그룹 내 베스트샷 선정**을 수행하는 GUI 프로그램.

## 절대 원칙 (위반 금지)
1. **비파괴성**: 자동 삭제 금지. 삭제는 항상 휴지통/격리 폴더 이동이 기본. 사용자 명시 확인 후에만 실행.
2. **재개 가능성**: 모든 진행 상태는 SQLite에 기록. 중단 후 재시작 시 이어서 진행.
3. **응답성**: 무거운 작업은 백그라운드(멀티프로세싱/QThread). UI 프리징 금지.
4. **한글 완전 지원**: 파일명·경로·EXIF UTF-8.

## 기술 스택
- Python 3.11+ / GUI: PySide6
- 이미지: Pillow, pillow-heif, rawpy, opencv-python
- 유사도: imagehash / 딥러닝: onnxruntime
- 저장: SQLite(WAL) / 병렬: multiprocessing
- 삭제 안전: send2trash / 패키징: pyinstaller

## 개발 순서 (docs/SPEC.md 5장 로드맵 기준)
- **Phase 1 (현재 시작점)**: 코어 엔진 — 디렉토리 스캔 + SQLite 스키마 + 완전 중복 검출 + EXIF 정규화 이미지 로더. CLI로 검증.
- Phase 2: perceptual hash 유사 검출 + 규칙 기반 분류
- Phase 3: 딥러닝 하이브리드 + 베스트샷 선정
- Phase 4: PySide6 GUI
- Phase 5: 안전 작업 + 재개 + 패키징

## 디렉토리 구조
```
photo-organizer/
├── CLAUDE.md              # 이 파일
├── docs/SPEC.md           # 상세 스펙 (반드시 참조)
├── src/photo_organizer/
│   ├── core/              # 스캔, DB, 해시 (Phase 1)
│   ├── classify/          # 분류, 유사도, 베스트샷 (Phase 2-3)
│   └── gui/               # PySide6 UI (Phase 4)
├── tests/                 # 테스트
├── models/               # ONNX 모델 파일 (분류/NIMA)
├── pyproject.toml
└── README.md
```

## 다음 할 일 (TODO)
**Phase 1~4 완료, Phase 5 안전 작업 핵심 완료.** 현재 상태·환경·남은 작업은
반드시 `docs/HANDOFF.md`를 먼저 읽으세요. 세부 체크리스트는 `docs/TODO.md`.

남은 작업(Phase 5 마감): 증분 재스캔(mtime) · PyInstaller 패키징(.exe/.app) ·
10만 장 성능/부하 테스트.

⚠️ 실행/테스트는 시스템 Python(3.9)이 아니라 **`.venv`(zerobrew python@3.14)** 로:
`PYTHONPATH=src .venv/bin/python -m pytest` / `... -m photo_organizer.gui.app`.

## 테스트 방침
- 각 core 모듈은 단위 테스트 동반 (pytest)
- 알려진 정답이 있는 소규모 샘플셋으로 정밀도/재현율 측정 (docs/SPEC.md 6장)
