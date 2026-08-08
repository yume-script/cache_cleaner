(function () {
  "use strict";

  // NOTE: 호스트가 플러그인 UI에 현재 plugin_id / db_type을 전역변수로 주입해
  // 준다는 보장이 없어서, 우선 합리적인 기본값 + URL 쿼리 폴백으로 처리한다.
  // 실제로 호스트가 다른 방식(예: data-* 속성)을 쓴다면 여기를 맞춰야 한다.
  var PLUGIN_ID = window.CURRENT_PLUGIN_ID || "cache_cleaner";
  var DB_TYPE =
    window.CURRENT_DB_TYPE ||
    new URLSearchParams(window.location.search).get("type") ||
    "general";

  var statusEl = document.getElementById("cc-status");
  var refreshBtn = document.getElementById("cc-refresh-btn");
  var cleanBtn = document.getElementById("cc-clean-btn");
  var cleanResultEl = document.getElementById("cc-clean-result");
  var saveBtn = document.getElementById("cc-save-btn");
  var saveResultEl = document.getElementById("cc-save-result");

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
  }

  function loadStatus() {
    statusEl.innerHTML = '<div class="cc-status-loading">불러오는 중...</div>';
    // 이미 동작이 확인된 위젯 데이터 엔드포인트를 재사용한다.
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

  function runCleanNow() {
    cleanBtn.disabled = true;
    cleanResultEl.textContent = "실행 중...";
    cleanResultEl.className = "cc-clean-result";

    // NOTE: 이 엔드포인트는 API 문서에 없는 추정 경로다. 실제 코어에
    // 플러그인 커스텀 액션을 받아줄 라우트가 없다면 404가 뜬다 —
    // 그 경우 백엔드에 라우트를 먼저 추가해야 한다.
    fetch("/api/media/plugins/" + PLUGIN_ID + "/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: DB_TYPE,
        action: "clean_now",
      }),
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        cleanBtn.disabled = false;
        if (data && data.success) {
          var r = data.result || {};
          cleanResultEl.textContent =
            "완료 — 모드: " +
            r.mode +
            ", 삭제 " +
            (r.deleted_count || 0) +
            "건" +
            (r.errors && r.errors.length ? " (오류 " + r.errors.length + "건)" : "");
          cleanResultEl.className = "cc-clean-result cc-result-ok";
          loadStatus();
        } else {
          cleanResultEl.textContent =
            "실패: " + ((data && data.error) || "알 수 없는 오류");
          cleanResultEl.className = "cc-clean-result cc-result-error";
        }
      })
      .catch(function () {
        cleanBtn.disabled = false;
        cleanResultEl.textContent =
          "액션 엔드포인트 호출에 실패했습니다. 백엔드에 " +
          "/api/media/plugins/" +
          PLUGIN_ID +
          "/action 라우트가 있는지 확인해주세요.";
        cleanResultEl.className = "cc-clean-result cc-result-error";
      });
  }

  function saveConfig() {
    var form = document.querySelectorAll(".cc-field input");
    var config = {};
    form.forEach(function (input) {
      config[input.name] = input.value;
    });

    saveBtn.disabled = true;
    saveResultEl.textContent = "저장 중...";
    saveResultEl.className = "cc-save-result";

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
        saveBtn.disabled = false;
        if (data && data.success) {
          saveResultEl.textContent = "저장되었습니다.";
          saveResultEl.className = "cc-save-result cc-result-ok";
          loadStatus();
        } else {
          saveResultEl.textContent =
            "저장 실패: " + ((data && data.error) || "알 수 없는 오류");
          saveResultEl.className = "cc-save-result cc-result-error";
        }
      })
      .catch(function () {
        saveBtn.disabled = false;
        saveResultEl.textContent = "저장 중 오류가 발생했습니다.";
        saveResultEl.className = "cc-save-result cc-result-error";
      });
  }

  refreshBtn.addEventListener("click", loadStatus);
  cleanBtn.addEventListener("click", runCleanNow);
  saveBtn.addEventListener("click", saveConfig);

  loadStatus();
})();
