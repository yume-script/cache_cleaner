(function () {
  "use strict";

  var PLUGIN_ID = window.CURRENT_PLUGIN_ID || "cache_cleaner";
  var DB_TYPE =
    window.CURRENT_DB_TYPE ||
    new URLSearchParams(window.location.search).get("type") ||
    "general";

  var statusEl = document.getElementById("cc-status");
  var logStatusEl = document.getElementById("cc-log-status");
  var logTabBtn = document.getElementById("cc-log-tab-btn");
  var statusTabsEl = document.getElementById("cc-status-tabs");
  var refreshBtn = document.getElementById("cc-refresh-btn");
  var cleanBtn = document.getElementById("cc-clean-btn");
  var cleanResultEl = document.getElementById("cc-clean-result");
  var saveBtn = document.getElementById("cc-save-btn");
  var saveResultEl = document.getElementById("cc-save-result");
  var enableLogCheckbox = document.getElementById("cc-enable-log");
  var logFieldsEl = document.getElementById("cc-log-fields");

  // ------------------------------------------------------------------
  // 로그 필드 접고 펼치기 (토글 스위치와 연동)
  // ------------------------------------------------------------------
  function syncLogFieldsVisibility() {
    if (!enableLogCheckbox || !logFieldsEl) return;
    if (enableLogCheckbox.checked) {
      logFieldsEl.classList.add("cc-open");
    } else {
      logFieldsEl.classList.remove("cc-open");
    }
  }

  if (enableLogCheckbox) {
    enableLogCheckbox.addEventListener("change", syncLogFieldsVisibility);
    syncLogFieldsVisibility();
  }

  // ------------------------------------------------------------------
  // 상태 탭 (캐시 / 로그)
  // ------------------------------------------------------------------
  function switchTab(tab) {
    if (!statusTabsEl) return;
    statusTabsEl.querySelectorAll(".cc-tab-btn").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    if (statusEl) statusEl.hidden = tab !== "cache";
    if (logStatusEl) logStatusEl.hidden = tab !== "log";
  }

  if (statusTabsEl) {
    statusTabsEl.addEventListener("click", function (e) {
      var btn = e.target.closest(".cc-tab-btn");
      if (!btn || btn.hidden) return;
      switchTab(btn.dataset.tab);
    });
  }

  function statusItem(label, value, warn) {
    return (
      '<div class="cc-status-item' +
      (warn ? " cc-status-warn" : "") +
      '">' +
      '<span class="cc-status-label">' +
      label +
      "</span>" +
      '<span class="cc-status-value">' +
      value +
      "</span>" +
      "</div>"
    );
  }

  function renderStatus(data) {
    var html = "";
    html += statusItem("캐시 경로", data.cache_dir);
    html += statusItem(
      "총 용량",
      data.size_gb + " GB / " + data.max_size_gb + " GB",
      data.will_full_clear
    );
    html += statusItem("총 파일 수", data.file_count + "개");
    html += statusItem(
      data.max_age_hours + "시간 이상 경과 파일",
      data.stale_count + "개"
    );
    html += statusItem(
      "실행 방식",
      data.cron_expr
        ? "Cron: " + data.cron_expr
        : "주기: " + data.interval_min + "분"
    );
    html += statusItem(
      "스케줄러",
      data.scheduler_backend === "apscheduler" ? "코어 스케줄러(APScheduler)" : "자체 스레드(폴백)"
    );
    html += statusItem("다음 실행 예정", data.next_run);
    html += statusItem("마지막 실행", data.last_run);
    html += statusItem(
      "마지막 결과",
      data.last_mode + " / 삭제 " + data.last_deleted_count + "건"
    );
    if (data.will_full_clear) {
      html +=
        '<div class="cc-status-item cc-status-warn">' +
        '<span class="cc-status-label">⚠ 다음 자동 점검 시</span>' +
        '<span class="cc-status-value">용량 초과로 전체 삭제됩니다</span>' +
        "</div>";
    }
    statusEl.innerHTML = html;

    renderLogStatus(data);
  }

  function renderLogStatus(data) {
    if (!logStatusEl || !logTabBtn) return;

    if (!data.log_enabled) {
      logTabBtn.hidden = true;
      if (!statusTabsEl.querySelector(".cc-tab-btn.active")) {
        switchTab("cache");
      } else if (statusTabsEl.querySelector('.cc-tab-btn[data-tab="log"]').classList.contains("active")) {
        switchTab("cache");
      }
      return;
    }
    logTabBtn.hidden = false;

    var html = "";
    html += statusItem("로그 경로", data.log_dir);
    html += statusItem(
      "총 용량",
      data.log_size_gb + " GB / " + data.log_max_size_gb + " GB",
      data.log_will_full_clear
    );
    html += statusItem("총 파일 수", data.log_file_count + "개");
    html += statusItem(
      data.log_max_age_hours + "시간 이상 경과 파일",
      data.log_stale_count + "개"
    );
    html += statusItem("마지막 실행", data.last_run);
    html += statusItem(
      "마지막 결과",
      data.log_last_mode + " / 삭제 " + data.log_last_deleted_count + "건"
    );
    if (data.log_will_full_clear) {
      html +=
        '<div class="cc-status-item cc-status-warn">' +
        '<span class="cc-status-label">⚠ 다음 자동 점검 시</span>' +
        '<span class="cc-status-value">용량 초과로 전체 삭제됩니다</span>' +
        "</div>";
    }
    logStatusEl.innerHTML = html;
  }

  function loadStatus() {
    statusEl.innerHTML = '<div class="cc-status-loading">불러오는 중...</div>';
    if (logStatusEl) {
      logStatusEl.innerHTML = '<div class="cc-status-loading">불러오는 중...</div>';
    }
    fetch(
      "/api/media/dashboard/widgets/" +
        PLUGIN_ID +
        "/data?type=" +
        encodeURIComponent(DB_TYPE)
    )
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data && data.success) {
          renderStatus(data);
        } else {
          statusEl.innerHTML =
            '<div class="cc-result-error">상태를 불러오지 못했습니다.</div>';
        }
      })
      .catch(function () {
        statusEl.innerHTML =
          '<div class="cc-result-error">상태 조회 중 오류가 발생했습니다.</div>';
      });
  }

  function currentFormConfig() {
    var form = document.querySelectorAll(".cc-settings-field input");
    var config = {};
    form.forEach(function (input) {
      if (input.type === "checkbox") {
        config[input.name] = input.checked;
      } else {
        config[input.name] = input.value;
      }
    });
    // toggle switch(cc-switch) 안의 체크박스는 .cc-settings-field 밖에 있어서
    // 위 querySelectorAll에 안 잡히므로 별도로 챙겨준다.
    if (enableLogCheckbox) {
      config[enableLogCheckbox.name] = enableLogCheckbox.checked;
    }
    return config;
  }

  function saveConfigPayload(config, onDone) {
    fetch("/api/media/metadata/plugins/save-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plugin_id: PLUGIN_ID,
        type: DB_TYPE,
        config: config,
      }),
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        onDone(null, data);
      })
      .catch(function (err) {
        onDone(err, null);
      });
  }

  function runCleanNow() {
    cleanBtn.disabled = true;
    cleanResultEl.textContent = "실행 요청 중...";
    cleanResultEl.className = "cc-clean-result";

    var config = currentFormConfig();
    var token = String(Date.now());
    config.RUN_NOW_TOKEN = token;

    saveConfigPayload(config, function (err, data) {
      if (err || !data || !data.success) {
        cleanBtn.disabled = false;
        cleanResultEl.textContent =
          "실행 요청 저장에 실패했습니다: " +
          (err ? err.message : (data && data.error) || "알 수 없는 오류");
        cleanResultEl.className = "cc-clean-result cc-result-error";
        return;
      }
      cleanResultEl.textContent = "실행 대기 중... (최대 수 초 소요)";
      pollForRunNowCompletion(token, 0);
    });
  }

  function pollForRunNowCompletion(token, attempt) {
    var MAX_ATTEMPTS = 12;
    fetch(
      "/api/media/dashboard/widgets/" +
        PLUGIN_ID +
        "/data?type=" +
        encodeURIComponent(DB_TYPE)
    )
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data && data.success && data.last_run_now_token === token) {
          cleanBtn.disabled = false;
          var mode = data.last_mode;
          var deleted = data.last_deleted_count || 0;
          var msg = "완료 — 모드: " + mode + ", 삭제 " + deleted + "건";
          if (data.log_enabled) {
            msg +=
              " (캐시) / 로그 " +
              (data.log_last_mode || "-") +
              " / 삭제 " +
              (data.log_last_deleted_count || 0) +
              "건 (로그)";
          }
          cleanResultEl.textContent = msg;
          cleanResultEl.className = "cc-clean-result cc-result-ok";
          renderStatus(data);
          return;
        }
        if (attempt >= MAX_ATTEMPTS) {
          cleanBtn.disabled = false;
          cleanResultEl.textContent =
            "응답이 지연되고 있습니다. 새로고침 버튼으로 상태를 다시 확인해주세요.";
          cleanResultEl.className = "cc-clean-result cc-result-error";
          return;
        }
        setTimeout(function () {
          pollForRunNowCompletion(token, attempt + 1);
        }, 2000);
      })
      .catch(function () {
        cleanBtn.disabled = false;
        cleanResultEl.textContent = "상태 확인 중 오류가 발생했습니다.";
        cleanResultEl.className = "cc-clean-result cc-result-error";
      });
  }

  function saveConfig() {
    var config = currentFormConfig();

    saveBtn.disabled = true;
    saveResultEl.textContent = "저장 중...";
    saveResultEl.className = "cc-save-result";

    saveConfigPayload(config, function (err, data) {
      saveBtn.disabled = false;
      if (!err && data && data.success) {
        saveResultEl.textContent = "저장되었습니다.";
        saveResultEl.className = "cc-save-result cc-result-ok";
        loadStatus();
      } else {
        saveResultEl.textContent =
          "저장 실패: " + (err ? err.message : (data && data.error) || "알 수 없는 오류");
        saveResultEl.className = "cc-save-result cc-result-error";
      }
    });
  }

  refreshBtn.addEventListener("click", loadStatus);
  cleanBtn.addEventListener("click", runCleanNow);
  saveBtn.addEventListener("click", saveConfig);

  loadStatus();
})();
