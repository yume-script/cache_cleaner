# -*- coding: utf-8 -*-
"""
cache_cleaner 플러그인
----------------------
특정 조건이 되면 cache 폴더를 정리하는 대시보드형 플러그인.

조건 01: 생성(수정)된 지 1일(24시간) 이상 지난 파일 -> 개별 삭제
조건 02: cache 폴더 전체 용량이 설정 임계값(기본 10GB) 이상 -> 폴더 내 파일 전체 삭제
(두 조건은 OR. 단, 조건 02가 만족되면 조건 01은 무시하고 전체 삭제)

## 실행 시점 확인 방법
이 플러그인은 "조회할 때만 상태를 보여주는" 방식이 아니라, 내부적으로
백그라운드 스레드를 하나 띄워 CHECK_INTERVAL_MINUTES 주기로 스스로
clean_cache()를 호출합니다. 실행 여부는 아래 두 곳에서 확인 가능합니다.

  1) 대시보드 위젯: 마지막 실행 시각 / 다음 실행 예정 시각 / 마지막 결과
  2) 로그 파일: <CACHE_DIR>/../cache_cleaner.log 에 실행마다 한 줄씩 기록
     (예: [2026-08-08 03:00:01] mode=age_based deleted=12 total_before_gb=3.21)

수동으로 즉시 실행하고 싶다면 clean_cache(db_type)를 직접 호출하면 됩니다.
"""

import os
import time
import json
import threading
from datetime import datetime

from plugins.metadata.base import BaseMetadataProvider


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
            "label": "자동 점검 주기 (분)",
            "type": "text",
            "required": False,
            "default": "60",
        },
    ]

    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/<org>/<repo>/<branch>/plugins/metadata/cache_cleaner",
        "files": ["cache_cleaner.py", "__init__.py", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }

    dashboard_widget = {
        "title": "Cache Cleaner",
        "subtitle": "캐시 폴더 용량/정리 현황",
        "provider": "Cache Cleaner",
        "icon": "fa-solid fa-broom",
        "limit": 6,
    }

    # 프로세스 내에서 db_type별 스케줄러 스레드가 중복 기동되지 않도록 관리
    _scheduler_threads = {}
    _scheduler_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 필수 인터페이스 (검색 미지원 플러그인)
    # ------------------------------------------------------------------
    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, "cache_cleaner는 대시보드/정리 전용 플러그인입니다."

    # ------------------------------------------------------------------
    # 활성화 시 스케줄러 기동
    # 코어가 플러그인 활성화 시점에 훅을 제공한다면 그 훅에서 호출하고,
    # 없다면 대시보드 최초 조회(get_dashboard_data) 시점에 지연 기동합니다.
    # ------------------------------------------------------------------
    def on_enable(self, db_type):
        self._ensure_scheduler(db_type)

    def _ensure_scheduler(self, db_type):
        with CacheCleanerMetadataProvider._scheduler_lock:
            existing = CacheCleanerMetadataProvider._scheduler_threads.get(db_type)
            if existing and existing.is_alive():
                return
            t = threading.Thread(
                target=self._scheduler_loop,
                args=(db_type,),
                daemon=True,
                name=f"cache-cleaner-{db_type}",
            )
            CacheCleanerMetadataProvider._scheduler_threads[db_type] = t
            t.start()

    def _scheduler_loop(self, db_type):
        while True:
            try:
                _cache_dir, _max_age, _max_size, interval_min = self._get_settings(db_type)
                result = self.clean_cache(db_type)
                self._log(db_type, result)
            except Exception as e:  # 스케줄러는 절대 죽지 않도록 방어
                self._log(db_type, {"mode": "error", "error": str(e)})
                interval_min = 60
            time.sleep(max(1, interval_min) * 60)

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

    def _log(self, db_type, result):
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

        # 대시보드 조회용 최신 상태 저장
        try:
            state = {"last_run": ts, "last_result": result}
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
    # 대시보드 데이터
    # ------------------------------------------------------------------
    def get_dashboard_data(self, db_type, limit=6):
        # 스케줄러가 아직 안 떠 있으면 여기서라도 기동 (지연 기동 폴백)
        self._ensure_scheduler(db_type)

        cache_dir, max_age_hours, max_size_gb, interval_min = self._get_settings(db_type)
        entries, total_size = self._scan(cache_dir)

        now = time.time()
        max_age_seconds = max_age_hours * 3600
        stale_count = sum(1 for _f, _s, m in entries if (now - m) >= max_age_seconds)
        size_gb = round(total_size / (1024 ** 3), 3)
        will_full_clear = size_gb >= max_size_gb

        state = self._read_state(cache_dir) or {}
        last_run = state.get("last_run", "아직 실행 안 됨")
        last_result = state.get("last_result") or {}

        items = [
            {"label": "캐시 경로", "value": cache_dir},
            {"label": "총 용량(GB)", "value": size_gb},
            {"label": "총 파일 수", "value": len(entries)},
            {"label": f"{int(max_age_hours)}시간 이상 경과 파일", "value": stale_count},
            {"label": "다음 자동 점검 주기(분)", "value": int(interval_min)},
            {"label": "마지막 실행", "value": last_run},
            {
                "label": "마지막 실행 결과",
                "value": f"{last_result.get('mode', '-')} / 삭제 {last_result.get('deleted_count', 0)}건",
            },
        ]

        return {"success": True, "items": items[:limit] if limit else items}
