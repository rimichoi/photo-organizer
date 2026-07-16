# 증분 재스캔 (Incremental Rescan) — 설계 문서

> 작성일: 2026-07-16 · Phase 5 마감 항목
> 관련 문서: `docs/HANDOFF.md` §5, `docs/SPEC.md` 4.3

## 1. 배경 / 문제

DB는 여러 root를 한곳에 쌓는 **누적 라이브러리** 모델이다(HANDOFF §4-4).
현재 스캔은 다음 공백이 있다.

1. `scanner.add_file`이 `INSERT OR IGNORE`(path UNIQUE)라서 **내용이 바뀐 파일이
   갱신되지 않는다.** size/mtime이 stale하고, 이미 계산된
   `content_hash`/`phash`/썸네일/분류가 낡은 채로 남는 **잠재 버그**.
2. **삭제된 파일 감지가 없다** — 디스크에서 사라져도 DB에 영구히 남아 뷰/리포트에
   유령 항목으로 노출된다.

목표: 재스캔 시 **신규 추가 · 변경 반영(파생 데이터 무효화) · 외부 삭제 감지**를
비파괴·재개 가능 원칙(HANDOFF §4-8, SPEC 절대원칙)을 지키며 수행한다.

## 2. 확정된 결정 사항

| # | 결정 | 근거 |
|---|------|------|
| D1 | "변경됨" = **size 또는 mtime 중 하나라도 다름** | rsync 등 표준 휴리스틱. mtime 단독보다 오탐/누락 모두 감소. scandir stat 재활용이라 추가 비용 0. |
| D2 | 외부 삭제는 **새 `missing` 플래그** | 사용자 정리(`removed`)와 외부 삭제를 의미 분리. 되돌리기/리포트 혼란 방지. |
| D3 | **`scan`을 증분으로 개선 + `--detect-deletions` 플래그** | scan의 기존 stale 버그를 근본 수정. 비용 큰 전체 대조는 옵션. |

## 3. 상세 설계

### 3.1 데이터 모델 (`core/database.py`)

- `files`에 컬럼 추가: `missing INTEGER DEFAULT 0`.
  - `_migrate()`에서 기존 DB에도 안전하게 `ALTER TABLE ... ADD COLUMN`
    (기존 `removed`/`batch`/`undone` 확장과 동일 패턴).
  - `missing=1` = 디스크에서 사라진 외부 삭제. 재스캔에서 파일이 다시 발견되면
    upsert가 `missing=0`으로 자동 복원.
  - `removed`(사용자 휴지통/격리 정리)와 **독립**. 되돌리기 로직은
    `action_log`의 `quarantine`만 대상이라 missing 파일은 영향 없음.
- **가시성 필터 추가**: `removed=0`을 쓰는 조회들에 `AND missing=0`을 병기해
  파이프라인·뷰·리포트에서 사라진 파일을 자동 제외한다. 대상(확인된 메서드):
  - `iter_size_duplicates` (dedup 후보)
  - `count_files`
  - `iter_duplicate_groups`
  - `iter_files_needing_analysis` (analyze 대상)
  - `iter_files_by_category`, `category_counts`
  - `iter_phashes`
  - `iter_similar_groups`
  - (GUI 조회 중 `removed=0`을 쓰는 곳이 있으면 동일 적용)

### 3.2 변경 감지 = upsert + 무효화 (`core/scanner.py` + `database.py`)

`add_file`의 `INSERT OR IGNORE`를 **SQLite upsert**(`ON CONFLICT(path) DO UPDATE`)로
교체한다. 파일당 1문(현재와 동일 비용), 배치 커밋 유지.

동작 분기:

- **신규**: INSERT (`scan_status='discovered'`, 기존과 동일).
- **기존 · 변경 없음** (`size` 동일 AND `mtime` 동일): `missing=0`만 보정하고
  나머지 컬럼은 유지. 파생 데이터 보존.
- **기존 · 변경됨** (`size` 다름 OR `mtime` 다름):
  - 갱신: `size`, `mtime`, `format`.
  - **파생 데이터 무효화**: `content_hash · phash · dhash · thumb_path ·
    category · category_confidence · error_msg = NULL`,
    `scan_status='discovered'`, `missing=0`.
  - 이후 파이프라인이 자연히 재처리:
    - dedup: 크기 그룹 재계산 시 포함.
    - analyze: `iter_files_needing_analysis`가 `phash IS NULL`을 잡음.
    - similar/bestshot: 전체 재작성(DELETE + rebuild).
  - 썸네일은 `{thumb_dir}/{file_id}.jpg`로 file_id 키잉 → 재분석이 같은 경로에
    덮어써 고아 파일이 생기지 않음.
- **`removed`는 건드리지 않는다**: 사용자 정리 상태는 actions 모듈 소관.
  (드문 엣지: 사용자가 정리한 파일이 원래 경로에 물리적으로 되살아나 재발견되면
  `missing=0`만 되고 `removed=1`은 유지되어 계속 숨김. 문서화된 의도적 동작.)

무효화 판정은 upsert의 `DO UPDATE SET ... = CASE WHEN <changed> THEN NULL ELSE
files.col END` 형태로 단일 SQL에 담는다. `<changed>` =
`files.size <> excluded.size OR files.mtime <> excluded.mtime`.

**mtime 비교는 exact(`<>`)**. 정밀도 지터로 인한 오탐이 나더라도 최악의 결과는
"불필요한 재분석"뿐이며 비파괴적이라 안전(데이터 손실 없음). 재분석 낭비가
문제가 되면 이후 초 단위 허용오차 도입을 검토.

### 3.3 삭제 감지 (`scan --detect-deletions`, 기본 off)

1. walk 중 root 하위에서 발견한 **디스크 경로 집합**을 수집(이미 순회 중이라 추가
   I/O 없음).
2. DB에서 **root 경로 접두어 하위**의 `missing=0 AND removed=0` 파일 경로를 조회.
3. 디스크 집합에 없는 DB 경로 → `missing=1`로 표시.
4. 재발견된 파일은 3.2의 upsert 경로에서 이미 `missing=0`으로 복원됨.

**스코프 제한**: root 접두어 매칭으로 다른 root의 파일을 삭제로 오탐하지 않는다.
경로 비교는 정규화된 구분자 기준 `path == root` 또는 `path.startswith(root + os.sep)`
(Python 측에서 수행; LIKE 와일드카드 이스케이프 이슈 회피).

🔒 **안전 가드 (핵심 요구사항)**: walk가 root 하위에서 **0개**를 발견하면
(네트워크 드라이브 언마운트/끊김, 권한 오류로 root 접근 불가 등) 삭제 감지를
**중단하고 경고를 반환**한다. 연결 끊긴 NAS가 라이브러리 전체를 missing으로
날리는 참사를 방지한다. (root 자체가 실제로 비어 정상적으로 0개인 경우와 구분이
어려우나, 안전을 위해 보수적으로 삭제를 보류한다.)

메모리: 10만 장 기준 디스크 경로 집합 + DB 경로 집합이 각각 수십 MB 수준으로
현재 규모에서 수용 가능(YAGNI — 필요 시 이후 스트리밍 대조로 최적화).

### 3.4 인터페이스

**CLI (`cli.py`)**
- `scan`은 자동 증분(변경 반영). `--detect-deletions` 플래그 추가.
- `scan_directory`가 요약 dict 반환: `{"new": N, "updated": N, "deleted": N}`
  (`deleted`는 감지 미수행/가드 발동 시 `None` 또는 0으로 구분).
- 완료 출력: `신규 N · 변경 N · 삭제 N` (가드 발동 시 경고 문구).

**GUI (`gui/workers.py`)**
- `PipelineWorker`에 `detect_deletions: bool = True` 파라미터 추가(라이브러리
  갱신 성격상 기본 활성). 기존 "폴더 추가/갱신" 버튼 흐름 그대로 재사용.
- missing 파일은 3.1 필터로 뷰에서 자동 숨김 — 추가 UI 작업 없음.
- 진행 메시지/요약에 변경·삭제 카운트 반영.

## 4. 테스트 계획 (pytest)

`tests/`에 증분 재스캔 테스트 추가(휴지통/실제 이동 오염 없이 tmp에서 수행).

- **변경 감지**: 신규만 / size 변경 / mtime 변경 / 무변경 각각에 대해 upsert
  결과와 파생 데이터(NULL) 무효화 검증.
- **재처리 연동**: 변경된 파일이 `iter_files_needing_analysis`에 잡히는지.
- **삭제 감지**: root 하위 파일만 missing, 다른 root 무영향, 재등장 시 복원.
- **안전 가드**: root 접근 불가/빈 walk 시 어떤 파일도 missing 처리되지 않음.
- **가시성 필터**: missing 파일이 dedup 후보·analyze 대상·그룹/카테고리
  조회·count에서 제외되는지.
- **요약 반환**: new/updated/deleted 카운트 정확성.

기존 68개 테스트가 계속 통과해야 한다(회귀 없음).

## 5. 비목표 (Non-goals)

- 실시간 파일시스템 감시(watchdog) — 명시적 재스캔만.
- 삭제된 파일의 자동 정리/DB 행 삭제 — missing 표시까지만(비파괴).
- mtime 초 단위 허용오차 — 필요성 확인 후 별도 검토.
- 삭제 감지의 스트리밍 대조 최적화 — 현재 규모에서 불필요(YAGNI).
