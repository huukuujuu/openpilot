# carrot-ev6 브랜치 변경 기록 — DM 기본값 Off

이 파일은 `carrot-ev6` 브랜치가 원본 `carrot-wip` 대비 무엇을 바꿨는지 기록한다.
`carrot-wip`을 계속 upstream으로 삼아 주기적으로 머지할 예정이므로, 이후 동기화에서 이
변경이 조용히 덮어써지지 않도록 하는 절차와 함께 유지한다.

- 브랜치: `carrot-ev6` (push: `origin/carrot-ev6`)
- 분기 기반: `carrot-wip` = `2f14e1e7` ("Update enrolled Carrot-B1 firmware automatically after Git Pull")
- 변경 커밋: `ae261f00` "Default driver monitoring to off"
- 기록 작성일: 2026-11-05
- 현재 브랜치 diff는 다음 명령로 언제든 다시 확인할 수 있다:
  `git diff carrot-wip...carrot-ev6`

## 목적

`DriverMonitoringEnabled`(DM/Driver Monitoring On/Off 영구 설정)의 **기본값을
ON→OFF**로 바꾼다. 신규 설치 또는 Params 초기화/reset-to-default 직후 DM이 꺼진
상태로 시작하게 하는 것이 목적이다. 기존에 값이 저장된 기기는 저장값을 유지한다
(PERSISTENT 파라미터).

주의: DM 끄기는 지역 법규/차량 요건 문제와 연결될 수 있어 원래는 권장되지 않는
설정이다(원본 카탈로그 설명문 참조). 이 브랜치는 EV6 차량용 예외 운영 브랜치다.

## 변경 파일 (4개)

| 파일 | 변경 내용 |
|---|---|
| `openpilot/common/params_keys.h` | `{"DriverMonitoringEnabled", {PERSISTENT, BOOL, "1"}}` → `"0"` (런타임 Params 기본값) |
| `openpilot/selfdrive/carrot_settings.json` | `DriverMonitoringEnabled` 항목 `"default": 1` → `0`, ko/en/zh 설명문("descr"/"edescr"/"cdescr")을 새 기본값 기준으로 수정. `search_only: true`, `risk: high`, `control: toggle`은 유지 |
| `openpilot/selfdrive/carrot/server/tests/test_settings_schema.py` | 테스트명 `..._default_on_...` → `..._default_off_...`, `(min,max,default)==(0,1,0)` 단언, params_keys.h 문자열 단언 `"0"` |
| `openpilot/selfdrive/carrot/server/tests/test_params_compat.py` | `test_reset_defaults_restores_driver_monitoring_enabled_to_on` → `..._to_off`, reset-to-default가 OFF("0")로 복원한다고 기대하는 단언으로 변경 |

## 코드 동작에 미치는 영향 (변경 없는 부분 포함)

- 바뀐 것은 **기본값 선언**뿐이다. DM on/off 판정 로직 자체는 그대로다:
  - `openpilot/selfdrive/monitoring/dm2d.py`: `DriverMonitoringEnabled && !DriverMonitoringSessionDisabled`
  - `openpilot/system/manager/process_config.py` `enable_dm_model`: 같은 조건으로 `dmonitoringmodeld` 실행 게이트
- 물리 CANCEL 3연속(3초 내) 세션 비활성 제스처, `DriverMonitoringMode`, SoftHold/정차
  DM 자동 일시정지(`docs/dm_soft_hold_pause_20261013.md`) 모두 불변.
- `DriverMonitoringEnabled`는 백업/QR 복원 제외(`BACKUP_EXCLUDED_PARAMS`) 정책이
  그대로라 백업 복원으로 다시 켜지지 않는다. Carrot Web에서는 검색으로만 노출
  (`search_only`)되는 상태도 유지.

## carrot-wip 동기화(머지) 절차

upstream `carrot-wip`을 따라올 때는 반드시 **실제 3-way merge**를 사용한다:

```
git fetch origin
git checkout carrot-ev6
git merge origin/carrot-wip
```

그리고 머지 후 이 파일의 변경 목록을 `git diff carrot-wip...carrot-ev6`로 다시
비교해 위 4개 파일의 DM 변경이 살아있는지 확인한다.

### 충돌/덮어쓰기 규칙

- `params_keys.h`의 `DriverMonitoringEnabled` 줄이나 `carrot_settings.json`의
  `DriverMonitoringEnabled` 항목(`default`/설명문)을 upstream이 바꾸면 충돌이
  날 수 있다. **해결 원칙: 기본값은 항상 `"0"`/`0` 유지.** upstream의 무관한
  다른 줄 변경(타 설정 추가, 설명문 오타 수정 등)은 그대로 반영한다.
- 테스트 두 개(`test_settings_schema.py`, `test_params_compat.py`)도 같은 원칙:
  DM 관련 단언은 OFF 기대값을 유지하고, 나머지 테스트 변경은 반영한다.
- **하면 안 되는 것**: `git checkout carrot-wip -- <file>`, `git reset --hard`,
  upstream 파일 통째 복사. 이렇게 하면 위 변경이 충돌 없이 조용히 사라진다.
  merge(3-way)는 같은 줄을 양쪽에서 바꾸지 않는 한 양쪽 변경을 함께 살린다.
- 이 문서 자체가 커밋으로 남아 있으므로, 실수로 사라져도 `git log --follow`로
  복구 기준을 찾을 수 있다.

### 머지 후 검증 (데스크톱, Windows)

아래 3개 파일의 84개 테스트가 통과해야 한다 (`ae261f00` 기준 통과 확인):

```
python -m pytest openpilot/selfdrive/carrot/server/tests/test_settings_schema.py ^
  openpilot/selfdrive/carrot/server/tests/test_params_compat.py ^
  openpilot/selfdrive/carrot/server/tests/test_setting_profiles.py ^
  -q --noconftest -o addopts=""
```

특히 보호해야 할 회귀 테스트:
- `test_settings_schema.py::test_driver_monitoring_setting_is_default_off_search_only_and_session_state_is_internal`
  (카탈로그 default 0 + params_keys.h `"0"` 문자열 단언)
- `test_params_compat.py::test_reset_defaults_restores_driver_monitoring_enabled_to_off`

이 두 테스트가 살아있는 한, 어느 시점에 기본값이 1로 되돌아가면 데스크톱 테스트와
CI가 실패로 잡아준다 (테스트까지 같이 덮어써진 경우에만 놓친다 — 그때는 이 파일로 확인).

## 대안 검토했던 다른 방법 (채택하지 않음)

- **rebase 방식** (`git rebase origin/carrot-wip`): 선형 히스토리가 깔끔해지지만
  carrot-ev6 히스토리를 다시 쓰므로 force push가 필요하고, 이미 공유된 브랜치엔
  부적합. 충돌 감지 능력은 merge와 같다.
- **`.gitattributes`의 `merge=ours` 드라이버**로 4개 파일을 보호: 충돌 시 우리
  쪽만 택하므로 정반대로 upstream의 해당 파일 개선을 조용히 버리는 위험. 파일
  단위 보호라 DM 한 줄 때문에 파일 전체 upstream 변경을 버리게 되어 기각.
- **별도 오버레이 파일**(branch 전용 파일에 default를 두는 방식): Params 기본값은
  컴파일되는 `params_keys.h`와 단일 카탈로그 JSON에 존재하므로 구조적으로 공유
  파일을 건드릴 수밖에 없어 불가. 대신 merge 시 충돌 지점이 위 4개 파일로 좁고
  명확해 관리 가능하다.
- **패치 파일 별도 보관**(`git diff carrot-wip...carrot-ev6 > ev6.patch` 후 매번
  재apply): 수동 재apply가 필요한데다 재apply 중에 같은 덮어쓰기 위험이 남으므로
  merge+회귀테스트가 더 안전. 필요할 때 언제든 같은 명령으로 재생성 가능하므로
  별도 보관은 생략.
