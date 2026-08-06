# 날짜 기준 년/월 폴더 정리 (Date Organize) — 설계 문서

> 작성일: 2026-08-06 · Phase 5 확장 항목
> 관련 문서: `docs/HANDOFF.md` §3·§4, `docs/SPEC.md` FR-08(카테고리별 폴더 정리)

## 1. 배경 / 문제

여러 디렉토리(여러 스캔 root)에 흩어진 사진을 **촬영 날짜 기준 년/월 폴더로 다시
모으는** 기능이 없다. 현재 앱은 중복·유사·분류·베스트샷까지 판단하고 휴지통/격리
이동은 하지만, "날짜별로 정리"는 CLI에도 GUI에도 없다.

이미 갖춰진 재료:

- `files.exif_dt` (EXIF 촬영시각 epoch초) — `analyze` 단계가 채운다
  (`classify/analyze.py:45`)
- 안전 이동 + 감사 로그 + 되돌리기 (`core/actions.py`, `action_log`)
- 이름 충돌 회피 (`actions._unique_dest`)

빠진 것: 파일명 날짜 파싱, 년/월 목적지 계산, 이동 실행 명령, `move` 액션의
되돌리기 지원.

## 2. 확정된 결정 사항

| 항목 | 결정 |
|------|------|
| 이동 방식 | **이동(move)**. 복사 아님. `action_log` 기록 → 되돌리기 지원 |
| 폴더 구조 | `<dest>/YYYY/YYYY-MM/` (예: `2023/2023-04/`) |
| 날짜 추정 실패 | `<dest>/_날짜미상/` 로. **mtime fallback 사용하지 않음** |
| 진입점 | **CLI 먼저** (`organize` 서브커맨드). GUI 연결은 이번 범위 밖 |
| 선행 단계 | `analyze` 선행 필수 (`exif_dt`를 그대로 사용, 자체 EXIF 재읽기 없음) |
| 대상 루트 | `--dest` **필수**. 암묵적 기본값 없음 |
| 동반 파일 | `.AAE`/`.MOV`/`.XMP` 등은 **이번 범위 밖** (이미지 파일만 이동) |

mtime을 쓰지 않는 이유: 복사·다운로드·NAS 이전으로 쉽게 바뀌어, 틀린 년월로
뿌려놓기보다 한 폴더에 모아 사용자가 수동 정리하는 편이 낫다.

## 3. 아키텍처

```
[analyze 완료된 DB]
        │  iter_files_for_organize()  (removed=0 AND missing=0)
        ▼
core/date_source.resolve_date(exif_dt, filename)   ← 순수 함수, 파일 I/O 없음
        │  (date, source)  source = exif | filename | unknown
        ▼
core/organize.plan_organize(db, dest_root)  → list[PlanItem]   (dry-run 출력)
        │
        ▼  --apply
core/organize.apply_organize(db, plan)
        │  shutil.move + action_log('move') + files.path 갱신
        ▼
core/actions.undo_last()  ← 'move' 배치도 원위치 복구
```

### 3.1 `core/date_source.py` (신규)

```python
resolve_date(exif_dt: float | None, filename: str) -> tuple[date | None, str]
# 반환: (날짜, 출처)   출처 = "exif" | "filename" | "unknown"
```

우선순위: `exif_dt` → 파일명 → `unknown`.

`exif_dt`는 epoch초이므로 `datetime.fromtimestamp()`로 로컬 날짜를 만든다.
`image_loader.parse_exif_datetime`이 EXIF의 로컬 시간 문자열을 로컬 타임존 기준
epoch으로 바꾸므로, 왕복하면 원래 촬영 시각이 그대로 복원된다.

파일명은 **확장자를 뗀 stem**에서 `YYYY[구분자]MM[구분자]DD`를 정규식으로 찾는다.
구분자는 없음 / `-` / `_` / `.` 를 허용한다. 커버 대상:

| 패턴 | 예 |
|------|-----|
| 안드로이드/카메라 | `IMG_20230412_183000.jpg`, `VID_20230412_183000.mp4` |
| 픽셀 | `PXL_20230412_183000123.jpg` |
| 카카오톡 | `KakaoTalk_20230412_183000.jpg` |
| 왓츠앱 | `IMG-20230412-WA0001.jpg` |
| macOS/iOS 스크린샷 | `Screenshot 2023-04-12 at 18.30.00.png` |
| 저장 시각 | `2023-04-12 18.30.00.jpg`, `20230412_183000.jpg` |

오탐 방지 3규칙:

1. **숫자 경계** — 구분자 없는 8자리는 앞뒤가 숫자면 매치하지 않는다.
   (`12345678901234` 같은 일련번호 차단)
2. **실제 파싱 검증** — `datetime`으로 파싱해 실패하면 탈락 (`20231345`, `20230230`)
3. **범위 제한** — 1990-01-01 ~ 오늘+1일 밖은 탈락 (epoch 숫자·해상도 문자열 차단)

매치가 여러 개면 **첫 번째**를 사용한다.

**알려진 한계(범위 밖)**: RAW(`.CR2`/`.NEF` 등)는 `analyze`가 촬영시각을 읽지
않는다(`image_loader.py:127` — RAW는 `dt` 없이 반환). 따라서 RAW는 파일명 경로로
넘어가고, 파일명에도 날짜가 없으면 `_날짜미상`으로 간다. RAW EXIF 날짜 읽기는
별도 개선 항목으로 남긴다.

### 3.2 `core/organize.py` (신규)

```python
@dataclass
class PlanItem:
    file_id: int
    src: str
    dest_dir: str        # <dest>/YYYY/YYYY-MM 또는 <dest>/_날짜미상
    source: str          # "exif" | "filename" | "unknown"
    skip: bool           # 이미 올바른 위치에 있음

plan_organize(db, dest_root: str, unknown_dir: str = "_날짜미상") -> list[PlanItem]
apply_organize(db, plan: list[PlanItem]) -> tuple[int, int, int]   # (moved, skipped, failed)
```

- 대상: `removed=0 AND missing=0` 인 모든 파일. 격리·휴지통 처리분과 유령 행 제외.
- 이미 목적 디렉토리에 있는 파일은 `skip=True` → 건드리지 않는다. 같은 명령을
  반복 실행해도 안전하고, **중단 후 재실행이 곧 재개**다(절대원칙 2).
- 실행은 검증된 `quarantine_files` 패턴을 따른다:
  배치 번호 발급 → **파일 단위 `try`로 예외 격리**(NFR-03) → 디렉토리 생성 →
  `shutil.move(normalize_long_path(...))` → `action_log`에 `move` 기록 →
  `files.path` 갱신.
- 이름 충돌은 `actions._unique_dest`를 재사용한다. private 이름을 모듈 밖에서 쓰지
  않도록 **`unique_dest`로 rename**하고 `actions.py`와 `organize.py`가 공유한다
  (rename 1곳, 호출부 2곳 수정).
- 크로스 디바이스 이동은 `shutil.move`가 copy+delete로 처리하므로 별도 대응 불필요.

### 3.3 `core/database.py` 확장

1. `iter_files_for_organize()` — `SELECT id, path, exif_dt FROM files
   WHERE removed=0 AND missing=0`
2. `update_paths(rows: list[tuple[str, int]])` — 이동 후 `files.path` 갱신.
   **`path`에 UNIQUE 제약이 있다.** 목적 경로가 과거에 스캔된 다른 행과 겹칠 수
   있으므로 파일 단위로 처리한다:
   - 충돌 행이 `missing=1`(유령) → 그 행과 의존 행(`duplicate_groups`,
     `similar_groups`, `action_log`)을 지우고 갱신. `_migrate`의 NFC 충돌 처리와
     동일한 정리 방식을 따른다.
   - 충돌 행이 살아있음 → 해당 파일만 `failed`로 집계하고 경로는 갱신하지 않는다
     (파일은 이미 이동됐으므로 다음 재스캔이 정리한다).
3. `last_undoable_batch()` — 조건을 `action IN ('quarantine', 'move')` 로 확장.

### 3.4 `core/actions.py` — 되돌리기 확장

`undo_last`가 `move` 배치도 처리한다.

- `move`: `to_path` → `from_path`로 되돌리고 `update_paths`로 DB 경로도 복원.
  `removed` 플래그는 애초에 건드리지 않았으므로 그대로 둔다.
- `quarantine`: 기존 동작 유지(파일 원위치 + `mark_removed(0)`).

기존 GUI '되돌리기' 버튼과 `Ctrl+Z`(`main_window.py:128`)는 `undo_last`를 호출하므로
**GUI 코드 변경 없이** 날짜 정리도 되돌릴 수 있다.

### 3.5 CLI

```bash
# 미리보기 (기본)
photo-organizer-cli --db lib.db organize --dest /Volumes/NAS/정리됨

# 실제 이동
photo-organizer-cli --db lib.db organize --dest /Volumes/NAS/정리됨 --apply
```

- `--dest` **필수**, `--apply` 없으면 **dry-run**, `--unknown-dir`로 `_날짜미상`
  폴더 이름 변경 가능.
- dry-run 출력: 출처별 집계(`exif 12,340 / filename 872 / 미상 41`), 년월별 건수,
  이동 예시 10줄, 그리고 "실제 이동하려면 `--apply`" 안내.
- `--apply` 출력: `이동 13,212 · 건너뜀 40 · 실패 1`.

## 4. 오류 처리

| 상황 | 처리 |
|------|------|
| 개별 파일 이동 실패(권한·네트워크 끊김) | `failed` 집계 후 다음 파일 계속 (NFR-03) |
| 목적 디렉토리 생성 실패 | 해당 파일만 실패 처리 |
| 같은 년월에 동일 파일명 | `unique_dest`로 ` (1)`, ` (2)` 부여 |
| 이미 목적 위치에 있음 | `skip` — 파일도 DB도 건드리지 않음 |
| DB `path` UNIQUE 충돌 | §3.3-2 규칙 |
| 긴 경로(Windows) | `normalize_long_path` 적용 |
| `dest_root`가 스캔 루트 하위 | 정상 동작. 재실행 시 `skip`으로 걸러짐 |

**비파괴성**: 완전삭제 없음. 모든 이동은 `action_log`에 남고 되돌릴 수 있다.

## 5. 테스트

`tests/test_date_source.py`

- EXIF 우선순위: `exif_dt`가 있으면 파일명 날짜를 무시한다
- 파일명 패턴 파라미터화: §3.1 표의 6개 패턴 전부
- 오탐 3종: `12345678901234`(숫자 경계), `20231345`·`20230230`(유효성),
  `18990101`(범위)
- 날짜 없음 → `(None, "unknown")`

`tests/test_organize.py` (`tmp_path`에 가짜 파일 생성 — `sample_photos` 직접 사용 금지)

- `plan_organize`: 목적 경로 계산, 출처 분류, `_날짜미상` 분기
- `apply_organize`: 파일 실제 위치, `files.path` 갱신, `action_log`에 `move` 기록
- 이름 충돌 → ` (1)` 리네임
- dry-run(plan만 호출)은 파일시스템을 변경하지 않는다
- `undo_last` → 파일 원위치 + DB 경로 복원
- `removed=1` / `missing=1` 파일은 계획에서 제외
- 재실행 시 전부 `skip`(멱등성)

기존 68개 테스트는 그대로 통과해야 한다(`unique_dest` rename 영향 확인 포함).

## 6. 범위 밖 (후속 항목)

- GUI 연결 (버튼·미리보기 다이얼로그·진행률 워커)
- 동반 파일(`.AAE`/`.MOV`/`.XMP`) 동반 이동
- RAW의 EXIF 촬영시각 읽기
- `mtime` fallback 옵션
- 사용자 지정 폴더 패턴(`{YYYY}/{MM}`)
