# -*- coding: utf-8 -*-
"""
cache_cleaner 플러그인
----------------------
특정 조건이 되면 cache 폴더를 정리하는 대시보드형 플러그인.

조건 01: 생성(수정)된 지 1일(24시간) 이상 지난 파일 -> 개별 삭제
조건 02: cache 폴더 전체 용량이 설정 임계값(기본 10GB) 이상 -> 폴더 내 파일 전체 삭제
(두 조건은 OR. 단, 조건 02가 만족되면 조건 01은 무시하고 전체 삭제)

## 실행 방식 (APScheduler 연동)
자체 스레드로 time.sleep 루프를 도는 대신, 코어가 이미 쓰고 있는
`services.scheduler_service.scheduler` (APScheduler BackgroundScheduler)
싱글톤에 잡을 등록해서 실행한다. 라이브러리 스캔 잡과 동일한 방식이며,
CRON_SCHEDULE 설정값을 주면 `CronTrigger.from_crontab()`으로 진짜 crontab
표현식을 그대로 쓸 수 있고, 비워두면 CHECK_INTERVAL_MINUTES 기반의 단순
반복(IntervalTrigger)으로 동작한다. 타임존도 코어 설정(TIMEZONE)을 그대로
따른다.

주의: 코어의 `SchedulerService.reload_all_jobs()`는 호출될 때마다 등록된
잡을 전부 지우고 라이브러리 스캔 잡만 재등록한다. 그래서 cache_cleaner
잡도 다른 라이브러리 작업(추가/수정 등)으로 reload가 발생하면 같이
지워질 수 있다. 이를 막기 위해 5분마다 잡 생존 여부만 가볍게 확인해서
없으면 재등록하는 워치독 스레드를 별도로 둔다 (실제 정리 작업은 여전히
APScheduler가 수행하고, 워치독은 등록 상태만 감시).

`services.scheduler_service`를 못 불러오는 환경(플러그인 샌드박스 등)이면
예전처럼 자체 스레드 루프로 폴백한다.

실행 여부는 아래에서 확인 가능하다.
  1) 설정 화면(settings.html): 마지막 실행 시각 / 결과
  2) 로그 파일: <CACHE_DIR>/../cache_cleaner.log
     (예: [2026-08-08 03:00:01] mode=age_based deleted=12 total_before_gb=3.21)

수동으로 즉시 실행하고 싶다면 clean_cache(db_type)를 직접 호출하면 된다.
"""

import os
import time
import json
import threading
from datetime import datetime

from plugins.metadata.base import BaseMetadataProvider


def run_cache_cleanup_job(db_type):
    """
    APScheduler가 직접 호출하는 모듈 레벨 함수.
    코어의 run_lazy_scanner_job()과 같은 스타일(top-level 함수)로 맞췄다.
    인스턴스 상태에 의존하지 않도록 매번 새 provider를 만들어 쓴다.
    """
    provider = CacheCleanerMetadataProvider()
    try:
        result = provider.clean_cache(db_type)
        provider._log(db_type, result)
    except Exception as e:
        provider._log(db_type, {"mode": "error", "error": str(e), "deleted_count": 0, "total_size_before": 0})


class CacheCleanerMetadataProvider(BaseMetadataProvider):
    id = "cache_cleaner"
    name = "Cache Cleaner"
    is_searchable = False

    config_schema = [
        {
            "key": "CACHE_DIR",
            "label": "캐시 폴더 경로",
            "type": "text",
            "required": True,
            "default": "cache",
        },
        {
            "key": "MAX_AGE_HOURS",
            "label": "파일 삭제 기준 (시간)",
            "type": "text",
            "required": False,
            "default": "24",
        },
        {
            "key": "MAX_SIZE_GB",
            "label": "전체 삭제 기준 용량 (GB)",
            "type": "text",
            "required": False,
            "default": "10",
        },
        {
            "key": "CHECK_INTERVAL_MINUTES",
            "label": "자동 점검 주기 (분) - CRON_SCHEDULE이 비어있을 때만 사용",
            "type": "text",
            "required": False,
            "default": "60",
        },
        {
            "key": "CRON_SCHEDULE",
            "label": "Cron 표현식 (예: 0 3 * * * = 매일 새벽 3시, 비우면 위 주기(분) 사용)",
            "type": "text",
            "required": False,
            "default": "",
        },
    ]

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/<org>/<repo>/<branch>/plugins/metadata/cache_cleaner",
        "files": ["cache_cleaner.py", "__init__.py", "VERSION", "settings.html", "settings.css", "settings.js"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }

    # 대시보드 카드 렌더러가 "도서 카드"(title/author/publisher/cover) 틀에
    # 고정돼 있어 통계성 정보와 안 맞으므로, 대시보드에는 노출하지 않는다.
    # 상태 확인/실행은 설정 화면(target=settings)에서 처리한다.
    dashboard_widget = None

    # 워치독 스레드(잡 생존 확인용) 및 폴백 스레드(스케줄러 모듈이 없을 때) 관리
    _watchdog_threads = {}
    _scheduler_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 필수 인터페이스 (검색 미지원 플러그인)
    # ------------------------------------------------------------------
    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "cache_cleaner는 대시보드/정리 전용 플러그인입니다."

    # ------------------------------------------------------------------
    # 활성화/비활성화 훅
    # 코어가 플러그인 활성화 시점에 부를 훅(on_enable/on_disable)을 제공하지
    # 않는다면, 대시보드/설정 화면 최초 조회 시점(get_status)에 지연
    # 등록되도록 그쪽에서도 _ensure_watchdog를 호출한다.
    # ------------------------------------------------------------------
    def on_enable(self, db_type):
        self._register_job(db_type)
        self._ensure_watchdog(db_type)
        self._ensure_run_now_poller(db_type)

    def on_disable(self, db_type):
        try:
            from services.scheduler_service import scheduler
            job = scheduler.get_job(self._job_id(db_type))
            if job:
                scheduler.remove_job(self._job_id(db_type))
        except Exception:
            pass

    @staticmethod
    def _job_id(db_type):
        return f"cache_cleaner_{db_type}"

    def _build_trigger(self, db_type):
        cfg = self.get_plugin_config(db_type, default={})
        cron_expr = (cfg.get("CRON_SCHEDULE") or "").strip()
        if cron_expr:
            from apscheduler.triggers.cron import CronTrigger
            return CronTrigger.from_crontab(cron_expr)
        _cache_dir, _max_age, _max_size, interval_min = self._get_settings(db_type)
        from apscheduler.triggers.interval import IntervalTrigger
        return IntervalTrigger(minutes=max(1, int(interval_min)))

    def _register_job(self, db_type):
        """
        코어 APScheduler 싱글톤에 잡을 등록한다. 코어 모듈을 못 불러오면
        (예: 플러그인이 별도 프로세스/샌드박스에서 도는 경우) 예전 방식의
        자체 스레드 루프로 폴백한다.
        """
        try:
            from services.scheduler_service import scheduler
        except Exception:
            self._register_fallback_thread(db_type)
            return False

        try:
            trigger = self._build_trigger(db_type)
        except ValueError as e:
            self._log(db_type, {"mode": "error", "error": f"잘못된 CRON_SCHEDULE: {e}",
                                 "deleted_count": 0, "total_size_before": 0})
            return False

        try:
            scheduler.add_job(
                run_cache_cleanup_job,
                trigger,
                id=self._job_id(db_type),
                args=[db_type],
                replace_existing=True,
                max_instances=1,
            )
            return True
        except Exception as e:
            self._log(db_type, {"mode": "error", "error": f"스케줄러 등록 실패: {e}",
                                 "deleted_count": 0, "total_size_before": 0})
            return False

    def _ensure_watchdog(self, db_type):
        """
        5분마다 '내 잡이 아직 등록돼 있는지'만 가볍게 확인하고, 없으면
        재등록한다. reload_all_jobs()가 모든 잡을 지우고 라이브러리 스캔
        잡만 재등록하는 코어 동작 때문에 필요하다.
        """
        with CacheCleanerMetadataProvider._scheduler_lock:
            existing = CacheCleanerMetadataProvider._watchdog_threads.get(db_type)
            if existing and existing.is_alive():
                return
            t = threading.Thread(
                target=self._watchdog_loop,
                args=(db_type,),
                daemon=True,
                name=f"cache-cleaner-watchdog-{db_type}",
            )
            CacheCleanerMetadataProvider._watchdog_threads[db_type] = t
            t.start()

    def _watchdog_loop(self, db_type):
        while True:
            try:
                from services.scheduler_service import scheduler
                if not scheduler.get_job(self._job_id(db_type)):
                    self._register_job(db_type)
            except Exception:
                # scheduler_service를 못 불러오는 환경이면 폴백 스레드가
                # 이미 실행 중인지만 확인한다.
                self._register_fallback_thread(db_type)
            time.sleep(300)

    # --- 폴백: 코어 스케줄러가 없는 환경용 자체 스레드 루프 ---
    def _register_fallback_thread(self, db_type):
        key = f"fallback_{db_type}"
        with CacheCleanerMetadataProvider._scheduler_lock:
            existing = CacheCleanerMetadataProvider._watchdog_threads.get(key)
            if existing and existing.is_alive():
                return
            t = threading.Thread(
                target=self._fallback_loop,
                args=(db_type,),
                daemon=True,
                name=f"cache-cleaner-fallback-{db_type}",
            )
            CacheCleanerMetadataProvider._watchdog_threads[key] = t
            t.start()

    def _fallback_loop(self, db_type):
        while True:
            try:
                _cache_dir, _max_age, _max_size, interval_min = self._get_settings(db_type)
                result = self.clean_cache(db_type)
                self._log(db_type, result)
            except Exception as e:
                self._log(db_type, {"mode": "error", "error": str(e),
                                     "deleted_count": 0, "total_size_before": 0})
                interval_min = 60
            time.sleep(max(1, interval_min) * 60)

    RUN_NOW_POLL_SECONDS = 5

    def _ensure_run_now_poller(self, db_type):
        """
        설정 화면의 "지금 즉시 삭제 실행" 버튼용. 백엔드에 별도 액션
        엔드포인트가 없으므로, 버튼 클릭 시 save-config로 config에
        RUN_NOW_TOKEN(타임스탬프)을 실어 보내고, 이 폴러가 5초마다 그
        토큰이 바뀌었는지 확인해서 바뀌었으면 즉시 clean_cache를 실행한다.
        완전한 즉시 실행은 아니고 최대 RUN_NOW_POLL_SECONDS초 지연이 있다.
        """
        key = f"runnow_{db_type}"
        with CacheCleanerMetadataProvider._scheduler_lock:
            existing = CacheCleanerMetadataProvider._watchdog_threads.get(key)
            if existing and existing.is_alive():
                return
            t = threading.Thread(
                target=self._run_now_poller_loop,
                args=(db_type,),
                daemon=True,
                name=f"cache-cleaner-runnow-{db_type}",
            )
            CacheCleanerMetadataProvider._watchdog_threads[key] = t
            t.start()

    def _run_now_poller_loop(self, db_type):
        while True:
            try:
                cfg = self.get_plugin_config(db_type, default={})
                token = str(cfg.get("RUN_NOW_TOKEN") or "").strip()
                if token:
                    cache_dir, *_ = self._get_settings(db_type)
                    state = self._read_state(cache_dir) or {}
                    if state.get("last_run_now_token") != token:
                        result = self.clean_cache(db_type)
                        self._log(db_type, result, extra={"last_run_now_token": token})
            except Exception as e:
                try:
                    self._log(db_type, {"mode": "error", "error": str(e),
                                         "deleted_count": 0, "total_size_before": 0})
                except Exception:
                    pass
            time.sleep(self.RUN_NOW_POLL_SECONDS)

    # ------------------------------------------------------------------
    # 설정 헬퍼
    # ------------------------------------------------------------------
    def _get_settings(self, db_type):
        cfg = self.get_plugin_config(db_type, default={})
        cache_dir = cfg.get("CACHE_DIR") or "cache"
        try:
            max_age_hours = float(cfg.get("MAX_AGE_HOURS") or 24)
        except (TypeError, ValueError):
            max_age_hours = 24.0
        try:
            max_size_gb = float(cfg.get("MAX_SIZE_GB") or 10)
        except (TypeError, ValueError):
            max_size_gb = 10.0
        try:
            interval_min = float(cfg.get("CHECK_INTERVAL_MINUTES") or 60)
        except (TypeError, ValueError):
            interval_min = 60.0
        return cache_dir, max_age_hours, max_size_gb, interval_min

    def _state_path(self, cache_dir):
        parent = os.path.dirname(os.path.abspath(cache_dir.rstrip("/\\"))) or "."
        return os.path.join(parent, "cache_cleaner_state.json")

    def _log_path(self, cache_dir):
        parent = os.path.dirname(os.path.abspath(cache_dir.rstrip("/\\"))) or "."
        return os.path.join(parent, "cache_cleaner.log")

    def _log(self, db_type, result, extra=None):
        cfg = self.get_plugin_config(db_type, default={})
        cache_dir = cfg.get("CACHE_DIR") or "cache"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 실행 이력 파일(로그) 기록
        try:
            line = (
                f"[{ts}] mode={result.get('mode')} "
                f"deleted={result.get('deleted_count', 0)} "
                f"total_before_gb={round(result.get('total_size_before', 0) / (1024**3), 3)} "
                f"errors={len(result.get('errors', []))}"
            )
            with open(self._log_path(cache_dir), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

        # 대시보드 조회용 최신 상태 저장 (기존 state에 extra를 병합해서 유지)
        try:
            prev_state = self._read_state(cache_dir) or {}
            state = {"last_run": ts, "last_result": result}
            if extra:
                state.update(extra)
            else:
                # extra가 없는 일반 실행이면 이전에 기록해둔 run_now 토큰 등은 보존
                for k in ("last_run_now_token",):
                    if k in prev_state:
                        state[k] = prev_state[k]
            with open(self._state_path(cache_dir), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError:
            pass

    def _read_state(self, cache_dir):
        path = self._state_path(cache_dir)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # 캐시 스캔
    # ------------------------------------------------------------------
    def _scan(self, cache_dir):
        entries = []
        total_size = 0
        if not os.path.isdir(cache_dir):
            return entries, total_size

        for root, _dirs, files in os.walk(cache_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    st = os.stat(fpath)
                except OSError:
                    continue
                entries.append((fpath, st.st_size, st.st_mtime))
                total_size += st.st_size

        return entries, total_size

    def _remove_empty_dirs(self, cache_dir):
        if not os.path.isdir(cache_dir):
            return
        for root, _dirs, _files in os.walk(cache_dir, topdown=False):
            if root == cache_dir:
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 정리 로직 (조건 01 / 조건 02) — 수동 호출도 가능
    # ------------------------------------------------------------------
    def clean_cache(self, db_type):
        cache_dir, max_age_hours, max_size_gb, _interval = self._get_settings(db_type)
        entries, total_size = self._scan(cache_dir)

        max_size_bytes = max_size_gb * (1024 ** 3)
        now = time.time()
        max_age_seconds = max_age_hours * 3600

        deleted_files = []
        errors = []

        if total_size >= max_size_bytes:
            mode = "size_exceeded"  # 조건 02
            for fpath, _size, _mtime in entries:
                try:
                    os.remove(fpath)
                    deleted_files.append(fpath)
                except OSError as e:
                    errors.append(f"{fpath}: {e}")
        else:
            mode = "age_based"  # 조건 01
            for fpath, _size, mtime in entries:
                if (now - mtime) >= max_age_seconds:
                    try:
                        os.remove(fpath)
                        deleted_files.append(fpath)
                    except OSError as e:
                        errors.append(f"{fpath}: {e}")

        self._remove_empty_dirs(cache_dir)

        return {
            "mode": mode,
            "cache_dir": cache_dir,
            "total_size_before": total_size,
            "deleted_count": len(deleted_files),
            "deleted_files": deleted_files,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # 상태 데이터 (설정 화면 JS가 소비)
    # ------------------------------------------------------------------
    def get_status(self, db_type):
        """
        캐시 상태 요약. dashboard_widget을 없앴으므로 홈 대시보드 카드용이
        아니라, 설정 화면(settings.js)이 직접 호출해서 쓰는 순수 데이터다.

        NOTE: 실제로 이 메서드를 어떤 라우트가 호출하게 할지는 코어 쪽 구현에
        달려 있다. 문서에 있는 기존 엔드포인트
        `/api/media/dashboard/widgets/<plugin_id>/data`가 dashboard_widget이
        없어도 이 메서드(또는 get_dashboard_data)를 그대로 불러주는지 확인
        필요. 안 불러준다면 별도 라우트를 코어에 추가해야 한다.
        """
        # on_enable 훅이 코어에 없을 경우를 대비한 지연 등록 폴백
        try:
            from services.scheduler_service import scheduler
            if not scheduler.get_job(self._job_id(db_type)):
                self._register_job(db_type)
        except Exception:
            self._register_fallback_thread(db_type)
        self._ensure_watchdog(db_type)
        self._ensure_run_now_poller(db_type)

        cache_dir, max_age_hours, max_size_gb, interval_min = self._get_settings(db_type)
        entries, total_size = self._scan(cache_dir)

        now = time.time()
        max_age_seconds = max_age_hours * 3600
        stale_count = sum(1 for _f, _s, m in entries if (now - m) >= max_age_seconds)
        size_gb = round(total_size / (1024 ** 3), 3)

        state = self._read_state(cache_dir) or {}
        last_run = state.get("last_run", "아직 실행 안 됨")
        last_result = state.get("last_result") or {}

        # 코어 APScheduler에 등록된 잡이면 다음 실행 예정 시각을 그대로 읽어온다.
        next_run = None
        scheduler_backend = "fallback_thread"
        try:
            from services.scheduler_service import scheduler
            job = scheduler.get_job(self._job_id(db_type))
            if job and job.next_run_time:
                next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                scheduler_backend = "apscheduler"
        except Exception:
            pass

        cfg = self.get_plugin_config(db_type, default={})
        cron_expr = (cfg.get("CRON_SCHEDULE") or "").strip()

        return {
            "success": True,
            "cache_dir": cache_dir,
            "size_gb": size_gb,
            "max_size_gb": max_size_gb,
            "file_count": len(entries),
            "stale_count": stale_count,
            "max_age_hours": max_age_hours,
            "interval_min": int(interval_min),
            "cron_expr": cron_expr or None,
            "will_full_clear": size_gb >= max_size_gb,
            "last_run": last_run,
            "last_mode": last_result.get("mode", "-"),
            "last_deleted_count": last_result.get("deleted_count", 0),
            "next_run": next_run or "확인 불가 (폴백 스레드 모드)",
            "scheduler_backend": scheduler_backend,
            "last_run_now_token": (state or {}).get("last_run_now_token"),
        }

    # 기존에 이미 동작이 확인된 엔드포인트
    # (`/api/media/dashboard/widgets/<plugin_id>/data`)를 그대로 재사용해서
    # 상태값을 실어 보낸다. dashboard_widget이 None이라 홈 화면 카드에는
    # 안 뜨지만, 데이터 API 자체는 살아있다는 가정하에 설정 화면의 JS가
    # 이 응답을 그대로 파싱해서 쓴다. items는 도서 카드 렌더러용 호환 필드라
    # 항상 빈 배열로 둔다.
    def get_dashboard_data(self, db_type, limit=6):
        status = self.get_status(db_type)
        status["items"] = []
        return status

    # ------------------------------------------------------------------
    # 커스텀 설정 화면
    # settings.html / settings.css / settings.js를 플러그인 루트에 두면
    # 코어가 파일명 규칙으로 자동 서빙한다 (config_schema 자동 폼 대신
    # 사용됨). 별도 서빙 메서드를 구현할 필요 없음 — 파일만 있으면 된다.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 설정 화면의 "지금 즉시 삭제" 버튼이 호출할 액션 처리
    # ------------------------------------------------------------------
    def handle_action(self, db_type, action, payload=None):
        """
        NOTE: 이 메서드를 호출해줄 백엔드 라우트가 API 문서에 없다.
        (`toggle` / `save-config` / `apply-metadata`만 존재)
        코어에 플러그인 커스텀 액션을 받아주는 공용 라우트가 있다면 그쪽에서
        이 메서드를 호출하도록 연결하고, 없다면 새로 추가해야 한다.
        예상 시그니처: handle_action(db_type: str, action: str, payload: dict)
        """
        if action == "clean_now":
            result = self.clean_cache(db_type)
            self._log(db_type, result)
            return {"success": True, "result": result}

        return {"success": False, "error": f"알 수 없는 action: {action}"}
