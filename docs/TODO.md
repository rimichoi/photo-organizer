# TODO — 단계별 구현 체크리스트

`docs/SPEC.md`의 로드맵을 실행 가능한 작업 단위로 분해한 것입니다. 위에서부터 순서대로 진행하세요.

## Phase 1 — 코어 엔진 (CLI 검증) ✅ 완료
- [x] `pyproject.toml` 의존성 정의 (Pillow, pillow-heif, imagehash, opencv-python, send2trash 등). PySide6/onnxruntime는 phase별 optional extras(`gui`/`dl`)로 분리
- [x] `core/database.py` — SQLite 스키마 생성 (files, duplicate_groups, similar_groups, scan_sessions, action_log), WAL 모드, 배치 커밋 헬퍼 + 세션/해시/중복그룹/리포트 메서드
- [x] `core/platform_utils.py` — (신규) 플랫폼 분기 격리: 긴 경로 `\\?\`/UNC, macOS·Windows 시스템 폴더 스킵. **Windows/macOS 동등 지원**
- [x] `core/image_loader.py` — EXIF Orientation 정규화 포함 이미지 로더, HEIC/RAW 대응, 손상 파일 graceful skip
- [x] `core/scanner.py` — 디렉토리 재귀 walk(scandir 스택), 이미지 확장자 필터, 크기/mtime을 DB에 기록, 세션 기록
- [x] `core/hasher.py` — 크기 그룹핑 → 빠른 해시(64KB) → SHA-256 완전 중복 검출, multiprocessing 병렬
- [x] `cli.py` — 스캔 실행 + 중복 리포트 출력 (CSV BOM/콘솔), scan/dedup/report 서브커맨드
- [x] Phase 1 단위 테스트: 해시 일관성, EXIF 회전, 손상 파일 처리, 스캐너, 플랫폼 유틸 (23 tests)

> **로컬 개발 환경(macOS)**: 시스템 Python 3.9뿐이라 zerobrew의 `python@3.14`로
> `.venv` 생성 후 코어 의존성 설치. 실행/테스트는 `PYTHONPATH=src`로:
> `PYTHONPATH=src .venv/bin/python -m pytest tests/`,
> `PYTHONPATH=src .venv/bin/python -m photo_organizer.cli --db lib.db scan <경로>`

## Phase 2 — 유사도 & 분류 ✅ 완료
- [x] `classify/phash.py` — pHash/dHash 계산 + 썸네일 생성 (순수 함수)
- [x] `classify/similar.py` — 해밍 거리 임계값 기반 유사 그룹 클러스터링 (**BK-tree** + union-find)
- [x] `classify/rules.py` — 규칙 기반 분류 (스크린샷/문서/블러/일반). 스크린샷은 파일명 패턴 + 화면 해상도 결합 신호로 정밀도 보호
- [x] `classify/analyze.py` — (신규) read-once 워커: 파일 1회 열기로 해시+썸네일+분류 동시 추출, multiprocessing 병렬, 재개 지원
- [x] `core/config.py` — 임계값 TOML 설정 로드 (`Config` dataclass, 워커 직렬화 지원)
- [x] `cli.py` — `analyze`/`similar` 서브커맨드 + `report --kind dup|similar|all` (CSV BOM)
- [x] 테스트: 리사이즈/재압축본 유사 판정, 블러 정오 판정, BK-tree↔전수비교 일치, 스크린샷/문서 규칙, 설정 라운드트립 (47 tests 누적)

> **분류 정확도 메모**: 규칙 신뢰도가 낮은 항목(예: EXIF 없는 사진 conf 0.6)은
> Phase 3 딥러닝 2차 확인 대상. `category_confidence`로 라우팅한다(SPEC 3.4).

## Phase 3 — 딥러닝 하이브리드 + 베스트샷 🟡 규칙 기반 완료 (AI 부분 보류)
- [x] `classify/quality.py` — 품질 지표 계산 (선명도/노출/대비/눈감음). 눈감음은 OpenCV 4.x 내장 Haar cascade(다운로드 불필요)
- [x] `classify/bestshot.py` — 지표 가중 합산, 그룹 내 베스트샷 선정 + 근거 JSON 생성, 인물/풍경 프리셋(얼굴 있으면 자동 person)
- [x] `cli.py` — `bestshot` 서브커맨드 + report에 ⭐/근거 표시
- [x] 테스트: 선명한 컷이 흔들린 컷을 이김, 근거 생성, 얼굴 없을 때 눈감음 지표 재정규화 (56 tests 누적)
- [ ] (보류) `classify/dl_classifier.py` — ONNX 분류 모델, 규칙 신뢰도 낮을 때만 호출
- [ ] (보류) NIMA 미적 점수 모델(ONNX) — 규칙 점수에 가중 합산으로 얹을 자리 마련됨
- [ ] (보류) `core/thumbnail_cache.py` — LRU 디스크 캐시 (현재는 단순 파일 캐시)

> **AI 보류 사유**: 사용자 결정으로 규칙 기반 우선 구현. ONNX 모델(분류/NIMA)은
> 모델 파일 확보 + onnxruntime용 Python 3.12 환경이 필요해 이후 진행. 베스트샷
> 핵심(선명도·노출·대비·눈감음)은 규칙만으로 완성됨.

## Phase 4 — GUI (PySide6) ✅ 1차 완료
- [x] `gui/main_window.py` — 메인 윈도우 + 폴더 선택 + "정리 시작" + 진행률 바/상태
- [x] `gui/workers.py` — 백그라운드 워커(QThread): 전체 파이프라인 실행, 진행 시그널, UI 프리징 없음
- [x] `gui/thumbnail_grid.py` — 가상 스크롤 썸네일 그리드(QListView + 지연 로딩 모델 + LRU 캐시, 10만 장 대응)
- [x] 중복/유사 그룹 검토 뷰 — 베스트샷 ⭐, 대표 ★, 근거 툴팁 표시
- [x] 카테고리별 탐색 뷰 (콤보 필터)
- [x] `gui/app.py` 진입점 + `photo-organizer-gui` gui-script, 헤드리스 GUI 스모크 테스트(offscreen)
- [x] **UX 1차**: 더블클릭 원본 열기(OS 기본 뷰어), 우클릭 메뉴(열기/폴더에서 보기/경로 복사), 빈 화면 안내, 그룹 배경 구분, 썸네일 크기 슬라이더, 라이트/다크 테마 대응
- [x] `gui/detail_panel.py` — 선택 상세 패널: 미리보기 + 분류/신뢰도 + 유사도 + 베스트샷 근거·품질지표 + **파일 크기 + EXIF(해상도·촬영기기·촬영일)**
- [x] '새로 시작' 버튼(전체 초기화) — 스캔은 '누적' 라이브러리 모델, 초기화로 비움
- [ ] (남은 개선) 네트워크 드라이브 자격증명 UI, ETA/속도 표시, 베스트샷 수동 교체(클릭), 삭제/이동 액션 연결(Phase 5)

> **UX 결정**: 스캔은 여러 폴더를 한 DB에 쌓는 '누적(라이브러리)' 모델(SPEC 재개 원칙).
> '새로 시작' 버튼으로 초기화. 앱은 DB가 없으면 빈 상태로 시작.
>
> **그룹 표시**: 중복/유사는 [그룹 헤더 + 그 그룹 사진 한 줄] 레이아웃(`GroupedGrid`)으로
> 그룹 경계를 명확히. 완전 중복의 비대표 멤버는 유사 탭에서 대표로 접어(collapse),
> 완전 중복이 유사 탭에 중복 노출되지 않게 함(각 문제는 한 탭에서만).

> **실행**: `PYTHONPATH=src .venv/bin/python -m photo_organizer.gui.app`
> (폴더 선택 → 정리 시작). 검증: offscreen 렌더링으로 각 탭 스크린샷 캡처해 확인 완료.
>
> **알려진 개선점**: 완전 중복 쌍이 유사 탭에도 나타남(바이트 동일 = phash 동일).
> 추후 유사 그룹에서 완전중복 멤버를 제외하는 정리 옵션 고려.

## Phase 5 — 안전 작업 & 마감 🟡 안전 핵심 완료
- [x] `core/actions.py` — **휴지통(send2trash)/격리 폴더 이동** + action_log 기록, 파일 단위 오류 격리
- [x] **되돌리기(undo)** — action_log 기반 격리 이동 원위치 복구 (휴지통은 OS에서 복구)
- [x] GUI 안전 작업 — 우클릭 '휴지통/격리', 그룹별 '여분 정리(대표·⭐ 제외)', **확인 다이얼로그**, '되돌리기' 버튼
- [x] `files.removed` 상태 컬럼(마이그레이션) — 정리된 파일은 모든 뷰에서 숨김, 복구 시 복원
- [x] 대표 원본 자동 추천 (완전중복: 최단 경로 / 유사: ⭐베스트샷)
- [x] CSV/JSON 내보내기 (CLI `report --csv/--json`, 한글 BOM/UTF-8)
- [ ] 증분 재스캔 (mtime 비교로 변경/신규만, 삭제 감지)
- [ ] PyInstaller 단일 exe(.exe)/.app 빌드 + 실행 안내
- [ ] 10만 장 규모 성능/부하 테스트, 네트워크 끊김 복구 테스트

> **안전 원칙**: 자동 완전삭제 없음. 삭제는 OS 휴지통(기본) 또는 격리 폴더 이동,
> 모든 작업 action_log 기록. GUI 확인 다이얼로그 필수. 테스트로 검증(휴지통은
> monkeypatch로 OS 휴지통 오염 방지, 격리+되돌리기 왕복 검증).
