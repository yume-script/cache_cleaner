# Cache Cleaner

BookOasis용 캐시 폴더 자동/수동 정리 플러그인.

- **버전**: 1.0.01
- **플러그인 고유 ID**: `cache_cleaner`

---

## 1. 기능

두 조건을 OR로 결합해서 캐시 폴더를 정리합니다.

| 조건 | 동작 |
| :--- | :--- |
| 조건 01: 파일이 생성(수정)된 지 일정 시간(기본 24시간) 이상 경과 | 해당 파일만 개별 삭제 |
| 조건 02: 캐시 폴더 총 용량이 기준치(기본 10GB) 이상 | 조건 01과 무관하게 폴더 내 파일 전체 삭제 |

- 조건 02가 조건 01보다 우선합니다. 용량 초과 시에는 파일 나이를 따지지 않고 전부 지웁니다.
- 삭제 후 남은 빈 하위 디렉토리는 정리하되, 캐시 폴더 자체는 유지합니다.
- 실행 이력은 로그 파일과 설정 화면에서 확인할 수 있습니다.
- 대시보드 홈 화면에는 노출되지 않습니다(대시보드 카드가 도서 카드 형식이라 통계 정보와 맞지 않아 제외). 상태 확인/설정/수동 실행은 전부 플러그인 설정 화면에서 합니다.

---

## 2. 설치 방법

BookOasis 서버의 `plugins/metadata/` 디렉토리에서 저장소를 클론합니다.

```bash
root@plex01:~/docker/BookOasis_stable/plugins/metadata# git clone https://github.com/yume-script/cache_cleaner
```

설치 후:

1. BookOasis 서버(컨테이너)를 재시작합니다.
2. 관리자 계정으로 로그인 후 **환경설정 > 플러그인 설정**으로 이동합니다.
3. 목록에서 **Cache Cleaner**를 찾아 활성화합니다.
4. 아래 설정값을 입력하고 저장합니다.

### 업데이트

`update_manifest`가 설정돼 있어 플러그인 관리 화면에서 샘플 업데이트 확인 버튼을 통해 최신 버전 여부를 확인할 수 있습니다. (`VERSION` 파일의 `plugin version` 값 기준)

수동 업데이트가 필요하면 플러그인 폴더에서 아래처럼 최신 커밋을 받으면 됩니다.

```bash
cd ~/docker/BookOasis_stable/plugins/metadata/cache_cleaner
git pull
```

받은 뒤에는 서버를 다시 재시작해야 반영됩니다.

---

## 3. 설정 항목

| 키 | 설명 | 기본값 |
| :--- | :--- | :--- |
| `CACHE_DIR` | 정리 대상 캐시 폴더 경로 (필수) | `cache` |
| `MAX_AGE_HOURS` | 조건 01 기준 시간 | `24` |
| `MAX_SIZE_GB` | 조건 02 기준 용량(GB) | `10` |
| `CHECK_INTERVAL_MINUTES` | `CRON_SCHEDULE`이 비어있을 때 사용하는 자동 점검 주기(분) | `60` |
| `CRON_SCHEDULE` | crontab 표현식(예: `0 3 * * *` = 매일 새벽 3시). 값이 있으면 이 스케줄이 우선하고 `CHECK_INTERVAL_MINUTES`는 무시됨 | (비어있음) |

설정은 플러그인 전용 화면(`settings.html`)에서 저장/조회하며, 별도 파일 없이도 `config_schema` 기준 자동 폼으로 대체될 수 있습니다.

---

## 4. 실행 방식

- 코어의 APScheduler 싱글톤(`services.scheduler_service.scheduler`)에 잡을 등록해서 실행합니다. 라이브러리 스캔 잡과 동일한 방식입니다.
  - `CRON_SCHEDULE`이 설정돼 있으면 `CronTrigger.from_crontab()`으로 등록됩니다.
  - 비어있으면 `CHECK_INTERVAL_MINUTES` 기반의 단순 반복(`IntervalTrigger`)으로 등록됩니다.
  - 타임존은 코어 설정(`TIMEZONE`)을 그대로 따릅니다.
- 코어 스케줄러 모듈을 불러올 수 없는 환경이면 자체 스레드 루프로 자동 폴백합니다.
- 5분마다 잡이 여전히 등록돼 있는지 확인하는 워치독이 함께 돌아갑니다. 코어의 `reload_all_jobs()`가 호출되면(예: 다른 라이브러리 추가/수정) 등록된 잡이 전부 초기화될 수 있는데, 이때 워치독이 재등록합니다.
- 설정 화면의 **"지금 즉시 삭제 실행"** 버튼을 누르면 저장 API에 실행 요청 토큰을 함께 실어 보내고, 내부 5초 주기 폴러가 이를 감지해 즉시 실행합니다. 완전한 즉시 실행은 아니며 최대 수 초의 지연이 있습니다.

---

## 5. 실행 여부 확인

1. **설정 화면**: 캐시 경로 / 총 용량 / 파일 수 / 경과 파일 수 / 실행 방식(Cron 또는 주기) / 사용 중인 스케줄러(코어 APScheduler 또는 폴백) / 다음 실행 예정 시각 / 마지막 실행 시각 / 마지막 결과가 표시됩니다.
2. **로그 파일**: `<CACHE_DIR 상위 폴더>/cache_cleaner.log`에 실행마다 한 줄씩 기록됩니다.
   ```
   [2026-08-08 03:00:01] mode=age_based deleted=12 total_before_gb=3.21 errors=0
   ```
3. **상태 파일**: 같은 위치의 `cache_cleaner_state.json`에 마지막 실행 결과가 JSON으로 저장됩니다(설정 화면이 이 파일을 읽어 보여줌).

---

## 6. 알려진 제약사항

- 대시보드 홈 화면에는 표시되지 않습니다(의도된 동작). 상태 확인은 설정 화면에서 해야 합니다.
- "지금 즉시 삭제 실행"은 전용 백엔드 액션 라우트가 없어 `save-config` 저장을 우회 신호로 사용하는 방식이라, 클릭 즉시가 아니라 최대 수 초의 지연이 있습니다.
- 코어가 플러그인 활성화/비활성화 시 호출해주는 훅(`on_enable`/`on_disable`)이 실제로 있는지는 아직 확정되지 않았습니다. 훅이 없다면 설정 화면을 최초로 열 때 지연 등록되는 폴백 경로를 탑니다.

---

## 7. 파일 구성

```
cache_cleaner/
  __init__.py
  cache_cleaner.py
  VERSION
  settings.html
  settings.css
  settings.js
  README.md
```
