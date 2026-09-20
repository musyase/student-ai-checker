const state = {
  runs: [],
  activeRun: null,
  rows: [],
  selectedIndex: null,
  selectedStudent: null,
  group: "all",
  status: "all",
  search: "",
  health: null,
  error: "",
  notice: "",
};

const app = document.querySelector("#app");
const API_BASE = ["tauri:", "asset:"].includes(window.location.protocol)
  || window.location.hostname === "tauri.localhost"
  ? "http://127.0.0.1:8765"
  : "";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
}

function api(path, options = {}) {
  return fetch(`${API_BASE}${path}`, { headers: { "Content-Type": "application/json" }, ...options }).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Ошибка ${response.status}`);
    return data;
  });
}

function statusClass(status) {
  if (status === "running") return "running";
  if (status === "Готово" || status === "Подтверждено" || status === "completed") return "completed";
  if (status === "Техническая ошибка") return "technical";
  return "review";
}

function statusText(status) {
  return ({ running: "Проверка идёт", completed: "Проверка завершена", failed: "Ошибка", pending: "Ожидает запуска" })[status] || status;
}

function render() {
  const run = state.activeRun;
  const summary = run ? run : null;
  app.innerHTML = `
    <header class="app-header">
      <div class="brand"><div class="brand-mark">SA</div><div><div class="brand-name">Student AI Checker</div><div class="brand-subtitle">локальное рабочее место преподавателя</div></div></div>
      <div class="header-actions">
        <div class="health"><span class="health-dot ${state.health?.ollama && state.health?.model_available ? "ok" : state.health ? "bad" : ""}"></span>${state.health ? (state.health.ollama ? (state.health.model_available ? "Ollama готова" : `Модель ${escapeHtml(state.health.model)} не найдена`) : "Ollama недоступна") : "Проверяем Ollama"}</div>
      </div>
    </header>
    <main class="app-shell">
      <div class="page-intro"><div><p class="eyebrow">Панель проверки</p><h1>Разберите работы по порядку</h1><p class="intro-copy">Запустите проверку, отфильтруйте нужную группу и пройдите только те критерии, где системе не хватило подтверждений.</p></div>${run ? '<button class="button secondary" id="new-run">Новая проверка</button>' : ""}</div>
      ${state.error ? `<div class="form-error">${escapeHtml(state.error)}</div>` : ""}
      ${state.notice ? `<div class="notice">${escapeHtml(state.notice)}</div>` : ""}
      ${run ? renderRun(run, summary) : renderSetup()}
    </main>`;
  bindEvents();
}

function renderSetup() {
  return `<section class="setup-panel"><div class="setup-head"><div><h2>Новая проверка</h2><p>Укажите папку с работами и файл задания. После запуска окно можно оставить открытым, прогресс обновится автоматически.</p></div></div><form id="run-form"><div class="form-grid"><div class="field full"><label for="folder">Папка с работами</label><div class="path-control"><input id="folder" name="folder" required placeholder="C:\\Users\\user\\Desktop\\Работы" /><button class="button secondary" id="pick-folder" type="button">Выбрать</button></div></div><div class="field full"><label for="assignment">Файл задания или чистый бланк</label><div class="path-control"><input id="assignment" name="assignment" required placeholder="C:\\Users\\user\\Desktop\\Работы\\ПР1.docx" /><button class="button secondary" id="pick-assignment" type="button">Выбрать</button></div></div><div class="field"><label for="model">Модель Ollama</label><input id="model" name="model" placeholder="по умолчанию из настроек" /></div><div class="field"><label for="workers">Параллельные проверки</label><select id="workers" name="workers"><option value="1">1 поток</option><option value="2" selected>2 потока</option><option value="3">3 потока</option><option value="4">4 потока</option></select></div></div><div class="form-actions"><button class="button primary" type="submit">Запустить проверку</button></div></form></section>`;
}

function renderRun(run, summary) {
  const groups = [...new Set(state.rows.map((row) => row["Группа"]))].filter(Boolean).sort((a, b) => a.localeCompare(b, "ru"));
  const visible = state.rows.filter((row) => {
    const groupOk = state.group === "all" || row["Группа"] === state.group;
    const statusOk = state.status === "all" || (state.status === "review" ? row["Статус"] !== "Готово" && row["Статус"] !== "Подтверждено" : row["Статус"] === "Готово" || row["Статус"] === "Подтверждено");
    const search = state.search.trim().toLocaleLowerCase("ru");
    const searchOk = !search || `${row["Фамилия / студенты"]} ${row["Группа"]}`.toLocaleLowerCase("ru").includes(search);
    return groupOk && statusOk && searchOk;
  });
  const done = Number(summary.completed_count || 0) + Number(summary.manual_review_count || 0) + Number(summary.failed_count || 0);
  const percent = summary.case_count ? Math.min(100, Math.round(done / summary.case_count * 100)) : 0;
  return `<div class="run-strip"><div class="run-meta"><div class="run-title">${escapeHtml(run.profile || "Проверка")}</div><div class="run-path" title="${escapeHtml(run.folder)}">${escapeHtml(run.folder)}</div>${run.status === "running" ? `<div class="progress"><div class="progress-bar" style="width:${percent}%"></div></div>` : ""}</div><div class="run-actions"><span class="status-chip ${statusClass(run.status)}">${escapeHtml(statusText(run.status))}</span><button class="button ghost" id="refresh-run">Обновить</button></div></div><div class="export-bar"><span>Экспорт результатов</span><a class="button secondary" href="/api/runs/${run.id}/exports/teacher-roster.csv" download>Реестр CSV</a><a class="button secondary" href="/api/runs/${run.id}/exports/groups-summary.csv" download>По группам</a><a class="button secondary" href="/api/runs/${run.id}/exports/results.csv" download>Все результаты</a></div><div class="metrics"><div class="metric"><div class="metric-label">Всего работ</div><div class="metric-value">${summary.case_count || 0}</div></div><div class="metric"><div class="metric-label">Готово</div><div class="metric-value">${summary.completed_count || 0}</div></div><div class="metric"><div class="metric-label">К проверке</div><div class="metric-value">${summary.manual_review_count || 0}</div></div><div class="metric"><div class="metric-label">Ошибки</div><div class="metric-value">${summary.failed_count || 0}</div></div></div><div class="workspace"><aside class="panel"><div class="panel-head"><h2>Группы</h2><p>${groups.length} групп</p></div><div class="group-list"><button class="group-button ${state.group === "all" ? "active" : ""}" data-group="all"><span>Все работы</span><span class="count">${state.rows.length}</span></button>${groups.map((group) => `<button class="group-button ${state.group === group ? "active" : ""}" data-group="${escapeHtml(group)}"><span>${escapeHtml(group)}</span><span class="count">${state.rows.filter((row) => row["Группа"] === group).length}</span></button>`).join("")}</div></aside><section class="panel"><div class="panel-head"><h2>Студенты</h2><p>${visible.length} в списке</p></div><div class="filter-row"><input class="filter-input" id="search" value="${escapeHtml(state.search)}" placeholder="Фамилия или группа" /><select class="filter-input" id="status"><option value="all" ${state.status === "all" ? "selected" : ""}>Все статусы</option><option value="review" ${state.status === "review" ? "selected" : ""}>Требуют проверки</option><option value="ready" ${state.status === "ready" ? "selected" : ""}>Готовые</option></select></div><div class="student-list">${visible.length ? visible.map(renderStudentRow).join("") : '<div class="empty">По выбранным фильтрам работ нет.</div>'}</div></section><section class="panel detail-panel">${state.selectedStudent ? renderDetail(state.selectedStudent) : '<div class="empty">Выберите студента слева, чтобы увидеть критерии, доказательства и решение преподавателя.</div>'}</section></div>`;
}

function renderStudentRow(row) {
  const selected = state.selectedIndex === row["Индекс"];
  const score = row["Итоговые баллы"] == null ? `${row["Известно баллов"] ?? "—"}/${row["Макс. баллов"] ?? "—"}` : `${row["Итоговые баллы"]}/${row["Макс. баллов"]}`;
  return `<button class="student-row ${selected ? "selected" : ""}" data-student="${row["Индекс"]}"><span><span class="student-name">${escapeHtml(row["Фамилия / студенты"])}</span><span class="student-group">${escapeHtml(row["Группа"])}</span></span><span class="student-score">${escapeHtml(score)}<span class="student-state">${escapeHtml(row["Статус"])}</span></span></button>`;
}

function renderDetail(payload) {
  const result = payload.result || {};
  const summary = (payload.review && payload.review.summary) || result.summary || {};
  const row = state.rows.find((item) => item["Индекс"] === payload.case.index) || {};
  const reviewDone = payload.review?.teacher_review?.status === "completed";
  const criteriaMap = Object.fromEntries((payload.criteria || []).map((item) => [item.id, item]));
  const total = reviewDone ? summary.final_total : summary.proposed_total;
  const grade = reviewDone ? summary.final_grade : summary.proposed_grade;
  return `<div class="detail-head"><div><h2 class="detail-name">${escapeHtml(row["Фамилия / студенты"] || payload.case.source_name)}</h2><p class="detail-sub">${escapeHtml(row["Группа"])} · <a class="doc-link" href="${payload.document_url}" target="_blank">Открыть исходный DOCX</a></p></div><div class="detail-score"><div class="big-score">${total ?? "—"}<span class="muted">/${summary.max_score ?? payload.case.max_score ?? "—"}</span></div><div class="big-grade">${reviewDone ? `Подтверждено: ${grade}` : (grade ? `Предварительно: ${grade}` : "Оценка пока не определена")}</div></div></div><div class="detail-body"><p class="section-label">Критерии и доказательства</p>${(result.results || []).map((criterion) => renderCriterion(criterion, criteriaMap[criterion.criterion_id])).join("")}<div class="review-box"><p class="section-label">Решение преподавателя</p>${(result.results || []).map((criterion) => renderDecision(criterion, criteriaMap[criterion.criterion_id], reviewDone)).join("")}<div class="detail-actions"><button class="button primary" id="save-review">Сохранить решение</button></div><p class="save-note" id="save-note"></p></div></div>`;
}

function renderCriterion(criterion) {
  const evidence = (state.selectedStudent.result?.resolved_evidence || []).filter((item) => (criterion.evidence_ids || []).includes(item.id));
  return `<article class="criterion"><div class="criterion-top"><div class="criterion-title">${escapeHtml(criterion.title)}</div><div class="criterion-score">${criterion.proposed_score == null ? "—" : `${criterion.proposed_score}/${criterion.max_score}`}</div></div><div class="criterion-meta">${escapeHtml(criterion.status_label_ru || criterion.status)} · ${escapeHtml(criterion.explanation || "")}</div>${criterion.manual_review_required ? `<div class="criterion-reason">${escapeHtml(criterion.manual_review_reason || "Требуется проверка преподавателя.")}</div>` : ""}${evidence.length ? `<div class="evidence-list">${evidence.map((item) => `<div class="evidence"><div class="evidence-id">${escapeHtml(item.id)} · ${escapeHtml(item.kind)}</div>${escapeHtml(item.text || "Изображение или техническое доказательство")}</div>`).join("")}</div>` : ""}</article>`;
}

function renderDecision(criterion, meta, reviewDone) {
  const allowed = meta?.allowed_scores || [0, 1, 2, 3, 4, 5].filter((score) => score <= criterion.max_score);
  const options = [];
  if (criterion.proposed_score != null) options.push(`<option value="confirm">Подтвердить ${criterion.proposed_score} балл.</option>`);
  allowed.forEach((score) => options.push(`<option value="score:${score}">Поставить ${score} балл.</option>`));
  return `<div class="decision-row"><label>${escapeHtml(criterion.title)}<br><span class="muted">Автоматически: ${criterion.proposed_score == null ? "нет балла" : `${criterion.proposed_score}/${criterion.max_score}`}</span></label><select class="decision-select" data-decision="${escapeHtml(criterion.criterion_id)}" ${reviewDone ? "disabled" : ""}>${options.join("")}</select></div>`;
}

async function loadRuns() {
  let data;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    try {
      await refreshHealth(false);
      data = await api("/api/runs");
      break;
    } catch (error) {
      if (attempt === 11) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  state.runs = data.runs || [];
  if (!state.activeRun && state.runs.length) await selectRun(state.runs[0].id);
}

function healthSignature(health) {
  return JSON.stringify({
    ollama: Boolean(health?.ollama),
    model: health?.model || "",
    model_available: Boolean(health?.model_available),
  });
}

async function refreshHealth(renderWhenChanged = true) {
  const previous = healthSignature(state.health);
  try {
    state.health = await api("/api/health");
  } catch (_error) {
    state.health = { ollama: false, model: "", model_available: false, models: [] };
  }
  if (renderWhenChanged && healthSignature(state.health) !== previous) render();
  return state.health;
}

async function selectRun(id) {
  state.activeRun = state.runs.find((run) => run.id === id) || null;
  state.error = "";
  state.rows = [];
  state.selectedStudent = null;
  state.selectedIndex = null;
  render();
  if (state.activeRun) await refreshRun();
}

async function refreshRun() {
  if (!state.activeRun) return;
  state.activeRun = await api(`/api/runs/${state.activeRun.id}`);
  const roster = await api(`/api/runs/${state.activeRun.id}/roster`);
  state.rows = roster.rows || [];
  if (state.selectedIndex != null) await selectStudent(state.selectedIndex, false);
  render();
}

async function selectStudent(index, rerender = true) {
  state.selectedIndex = Number(index);
  state.selectedStudent = await api(`/api/runs/${state.activeRun.id}/students/${index}`);
  if (rerender) render();
}

function bindEvents() {
  document.querySelector("#new-run")?.addEventListener("click", () => { state.activeRun = null; state.rows = []; state.selectedStudent = null; state.error = ""; render(); });
  document.querySelector("#refresh-run")?.addEventListener("click", () => refreshRun().catch(showError));
  document.querySelectorAll("[data-group]").forEach((button) => button.addEventListener("click", () => { state.group = button.dataset.group; render(); }));
  document.querySelectorAll("[data-student]").forEach((button) => button.addEventListener("click", () => selectStudent(button.dataset.student).catch(showError)));
  document.querySelector("#search")?.addEventListener("input", (event) => { state.search = event.target.value; render(); const input = document.querySelector("#search"); input?.focus(); input?.setSelectionRange(state.search.length, state.search.length); });
  document.querySelector("#status")?.addEventListener("change", (event) => { state.status = event.target.value; render(); });
  document.querySelector("#pick-folder")?.addEventListener("click", () => pickPath("folder"));
  document.querySelector("#pick-assignment")?.addEventListener("click", () => pickPath("file"));
  document.querySelector("#run-form")?.addEventListener("submit", async (event) => { event.preventDefault(); state.error = ""; const form = new FormData(event.target); try { const run = await api("/api/runs", { method: "POST", body: JSON.stringify({ folder: form.get("folder"), assignment: form.get("assignment"), model: form.get("model"), workers: Number(form.get("workers")) }) }); state.activeRun = run; state.runs.unshift(run); await refreshRun(); } catch (error) { showError(error); } });
  document.querySelector("#save-review")?.addEventListener("click", saveReview);
}

async function pickPath(kind) {
  try {
    const data = await api("/api/pick", { method: "POST", body: JSON.stringify({ kind }) });
    if (data.path) document.querySelector(kind === "folder" ? "#folder" : "#assignment").value = data.path;
  } catch (error) { showError(error); }
}

async function saveReview() {
  const decisions = {};
  document.querySelectorAll("[data-decision]").forEach((select) => { const value = select.value; decisions[select.dataset.decision] = value === "confirm" ? { action: "confirm" } : { action: "set_score", score: Number(value.split(":")[1]) }; });
  try { const data = await api(`/api/runs/${state.activeRun.id}/students/${state.selectedIndex}/review`, { method: "POST", body: JSON.stringify({ decisions }) }); state.notice = data.summary.final_grade ? `Решение сохранено. Итоговая оценка: ${data.summary.final_grade}.` : "Решение сохранено, но часть критериев ещё требует решения."; await refreshRun(); await selectStudent(state.selectedIndex); } catch (error) { showError(error); }
}

function showError(error) { state.error = error.message || String(error); render(); }

loadRuns().then(render).catch(showError);
setInterval(() => {
  refreshHealth().catch(showError);
  if (state.activeRun?.status === "running") refreshRun().catch(showError);
}, 2500);
