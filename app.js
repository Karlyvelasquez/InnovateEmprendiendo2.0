const state = {
  authenticated: false,
  user: null,
  rubrics: [],
  dashboard: null,
  role: null,
  activeTeamId: null,
  juryDraft: null,
  juryDirty: false,
  modalOpen: false,
  loading: false,
  teamSearch: "",
  toastTimer: null,
  deliberating: false,
  deliberationOrder: null,
  deliberationSaving: false,
  confirmingDeliberation: false,
  confirmingResetEvaluations: false,
  resettingEvaluations: false,
  aiImproving: false,
  aiOriginalByTeam: {},
};

const dom = {
  authScreen: document.getElementById('auth-screen'),
  appScreen: document.getElementById('app-screen'),
  loginForm: document.getElementById('login-form'),
  loginIdentifier: document.getElementById('login-identifier'),
  loginPassword: document.getElementById('login-password'),
  togglePassword: document.getElementById('toggle-password'),
  loginFeedback: document.getElementById('login-feedback'),
  roleBadge: document.getElementById('auth-role-badge'),
  userName: document.getElementById('user-name'),
  userRole: document.getElementById('user-role'),
  dashboardRoot: document.getElementById('dashboard-root'),
  logoutButton: document.getElementById('logout-button'),
  refreshButton: document.getElementById('refresh-button'),
  toast: document.getElementById('toast'),
  teamModal: document.getElementById('team-modal'),
  teamSearch: document.getElementById('team-search'),
  teamSearchResults: document.getElementById('team-search-results'),
};

const rubricTitles = {
  problem_score: 'Identificación del problema',
  value_score: 'Propuesta de valor',
  validation_score: 'Validación de la solución',
  business_score: 'Modelo de negocio',
  pitch_score: 'Calidad del pitch',
};

const scoreFields = ['problem_score', 'value_score', 'validation_score', 'business_score', 'pitch_score'];

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function richText(value) {
  return escapeHtml(value).replace(/\n/g, '<br />');
}

function formatNumber(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return numeric.toFixed(digits);
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('es-CO', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function getUniversityLabel(team) {
  if (!team) return '—';
  return team.filial ? `${team.university} · ${team.filial}` : team.university;
}

// Etiqueta sutil de "día" (19 o 20 de agosto). Es solo informativa: no
// cambia el orden del ranking, que sigue siendo una única competencia.
const DAY_BADGE_STYLES = {
  '19 de agosto': { label: '19 ago', bg: '#eef2ff', fg: '#4338ca', border: '#c7d2fe' },
  '20 de agosto': { label: '20 ago', bg: '#fff7ed', fg: '#c2410c', border: '#fed7aa' },
};

function dayBadge(team) {
  const day = team?.schedule_day;
  const style = DAY_BADGE_STYLES[day];
  if (!style) return '';
  return `<span class="day-chip" title="Presentó el ${escapeHtml(day)} · no afecta el ranking" style="display:inline-block;margin-left:6px;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;letter-spacing:.02em;background:${style.bg};color:${style.fg};border:1px solid ${style.border};white-space:nowrap;vertical-align:middle;">${style.label}</span>`;
}

function getStatusClass(status) {
  return `status-pill`;
}

function setScreen(authenticated) {
  dom.authScreen.classList.toggle('active', !authenticated);
  dom.appScreen.classList.toggle('active', authenticated);
}

function scrollDashboardToTop() {
  if (dom.dashboardRoot && typeof dom.dashboardRoot.scrollIntoView === 'function') {
    dom.dashboardRoot.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setToast(message, type = 'info') {
  clearTimeout(state.toastTimer);
  dom.toast.textContent = message;
  dom.toast.className = `toast visible ${type}`;
  state.toastTimer = window.setTimeout(() => {
    dom.toast.className = 'toast';
  }, 2800);
}

function setLoginFeedback(message) {
  dom.loginFeedback.textContent = message;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || 'Ha ocurrido un error');
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function startResetEvaluations() {
  if (!state.user?.can_reset_evaluations) return;
  state.confirmingResetEvaluations = true;
  render();
}

function cancelResetEvaluations() {
  state.confirmingResetEvaluations = false;
  render();
}

async function confirmResetEvaluations() {
  if (state.resettingEvaluations) return;
  state.resettingEvaluations = true;
  render();
  try {
    const response = await api('/api/admin/reset-evaluations', { method: 'DELETE' });
    state.dashboard = response.dashboard;
    state.confirmingResetEvaluations = false;
    state.deliberating = false;
    state.deliberationOrder = null;
    setToast(response.message || 'Calificaciones reiniciadas.');
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.resettingEvaluations = false;
    render();
  }
}

function startDeliberation() {
  const ranking = state.dashboard?.ranking || [];
  if (!ranking.length) return;
  state.confirmingDeliberation = true;
  render();
}

function cancelConfirmDeliberation() {
  state.confirmingDeliberation = false;
  render();
}

function confirmStartDeliberation() {
  const ranking = state.dashboard?.ranking || [];
  state.deliberationOrder = ranking
    .slice()
    .sort((a, b) => (a.position || 0) - (b.position || 0))
    .map((team) => team.id);
  state.deliberating = true;
  state.confirmingDeliberation = false;
  render();
}

function cancelDeliberation() {
  state.deliberating = false;
  state.deliberationOrder = null;
  render();
}

function reorderDeliberation(fromIndex, toIndex) {
  if (!state.deliberationOrder) return;
  if (toIndex < 0 || toIndex >= state.deliberationOrder.length || fromIndex === toIndex) return;
  const updated = [...state.deliberationOrder];
  const [moved] = updated.splice(fromIndex, 1);
  updated.splice(toIndex, 0, moved);
  state.deliberationOrder = updated;
  render();
}

async function saveDeliberation() {
  if (!state.deliberationOrder || state.deliberationSaving) return;
  state.deliberationSaving = true;
  render();
  try {
    const response = await api('/api/admin/ranking', {
      method: 'PUT',
      body: JSON.stringify({ order: state.deliberationOrder }),
    });
    state.dashboard = response.dashboard;
    state.deliberating = false;
    state.deliberationOrder = null;
    setToast(response.message || 'Deliberación guardada.');
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.deliberationSaving = false;
    render();
  }
}

let deliberationDragIndex = null;

function handleDeliberationDragStart(event) {
  const card = event.target.closest('.deliberation-card');
  if (!card) return;
  deliberationDragIndex = Number(card.dataset.deliberationIndex);
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(deliberationDragIndex));
  }
  card.classList.add('dragging');
}

function handleDeliberationDragOver(event) {
  const card = event.target.closest('.deliberation-card');
  if (!card || deliberationDragIndex === null) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
}

function handleDeliberationDrop(event) {
  const card = event.target.closest('.deliberation-card');
  if (!card || deliberationDragIndex === null) return;
  event.preventDefault();
  const targetIndex = Number(card.dataset.deliberationIndex);
  const fromIndex = deliberationDragIndex;
  deliberationDragIndex = null;
  reorderDeliberation(fromIndex, targetIndex);
}

function handleDeliberationDragEnd() {
  deliberationDragIndex = null;
  document.querySelectorAll('.deliberation-card.dragging').forEach((el) => el.classList.remove('dragging'));
}

async function exportWinnersExcel() {
  try {
    const response = await fetch('/api/admin/export-winners', {
      credentials: 'same-origin',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'No se pudo generar el Excel de ganadores.');
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : 'ganadores_top20.xlsx';
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    setToast(error.message, 'error');
  }
}

async function exportAllTeamsObservationsPdf() {
  try {
    const response = await fetch('/api/admin/export-pdf', {
      credentials: 'same-origin',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'No se pudo generar el PDF.');
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : 'observaciones_todos_los_equipos.pdf';
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    setToast(error.message, 'error');
  }
}

function rubricLabel(rubric) {
  return `${rubric.label} — ${Math.round(rubric.weight * 100)}%`;
}

function teamSummaryText(team) {
  if (!team) return '—';
  return `${team.name} · ${team.country || 'Sin país'}`;
}

function isValidScoreValue(value) {
  return typeof value === 'number' && Number.isFinite(value) && VALID_SCORE_VALUES.includes(value);
}

function isDraftComplete(draft) {
  const scoresOk = scoreFields.every((key) => isValidScoreValue(draft?.scores?.[key]));
  const observationsOk = Boolean((draft?.observations || '').trim());
  return scoresOk && observationsOk;
}

function buildDraftFromEvaluation(evaluation) {
  const scores = {};
  for (const key of scoreFields) {
    scores[key] = isValidScoreValue(evaluation?.[key]) ? evaluation[key] : null;
  }
  return {
    scores,
    observations: evaluation?.observations || '',
  };
}

function syncSessionState(payload) {
  state.authenticated = Boolean(payload.authenticated);
  state.user = payload.user || null;
  state.rubrics = payload.rubrics || [];
  state.dashboard = payload.dashboard || null;
  state.role = state.user?.role || null;
  state.activeTeamId = state.role === 'admin'
    ? state.dashboard?.selected_team?.team?.id || state.dashboard?.ranking?.[0]?.id || null
    : state.dashboard?.current_team_id || null;
  state.juryDraft = state.role === 'jury' ? buildDraftFromEvaluation(state.dashboard?.current_evaluation) : null;
  state.juryDirty = false;
}

function renderShell() {
  if (!state.authenticated) {
    setScreen(false);
    dom.roleBadge.textContent = 'Sesión no iniciada';
    return;
  }

  setScreen(true);
  dom.userName.textContent = state.user?.name || 'Usuario';
  dom.userRole.textContent = state.user?.role === 'admin' ? 'Administrador' : 'Jurado';
  dom.roleBadge.textContent = state.user?.role === 'admin' ? 'Acceso de administrador' : 'Acceso de jurado';
}

function render() {
  renderShell();
  if (!state.authenticated) {
    dom.dashboardRoot.innerHTML = '';
    return;
  }

  if (state.role === 'admin') {
    dom.dashboardRoot.innerHTML = renderAdminDashboard();
  } else {
    dom.dashboardRoot.innerHTML = renderJuryDashboard();
  }
}

function renderAdminDashboard() {
  const dashboard = state.dashboard || {};
  const ranking = dashboard.ranking || [];
  const selected = dashboard.selected_team;
  const summary = dashboard.summary || { teams: 0, evaluated: 0, pending: 0, jurors: 0 };
  const selectedTeam = selected?.team || null;
  const selectedRanking = selectedTeam?.position || '—';
  const selectedEvaluations = selected?.evaluations || [];

  const summaryCards = `
    <section class="summary-grid">
      <article class="summary-card">
        <span>Total de equipos</span>
        <div class="value">${summary.teams || 0}</div>
      </article>
      <article class="summary-card">
        <span>Evaluados al menos una vez</span>
        <div class="value">${summary.evaluated || 0}</div>
      </article>
      <article class="summary-card">
        <span>Pendientes</span>
        <div class="value">${summary.pending || 0}</div>
      </article>
      <article class="summary-card">
        <span>Jurados activos</span>
        <div class="value">${summary.jurors || 0}</div>
      </article>
    </section>
  `;

  const WINNER_CUTOFF = 20;
  const rankingById = new Map(ranking.map((team) => [team.id, team]));

  const rankingRows = ranking.length
    ? ranking.map((team) => `
        <tr class="${team.id === selectedTeam?.id ? 'active' : ''} ${(team.position || 0) <= WINNER_CUTOFF ? 'qualifies' : ''}" data-action="select-admin-team" data-team-id="${team.id}">
          <td><strong>#${team.position || '—'}</strong></td>
          <td>
            <div><strong>${escapeHtml(team.name)}</strong>${dayBadge(team)}</div>
            <div class="muted">${escapeHtml(getUniversityLabel(team))}</div>
          </td>
          <td>${formatNumber(team.average_5, 2)}</td>
          <td>${formatNumber(team.average_100, 1)}</td>
          <td>${team.evaluation_count}</td>
          <td><span class="${getStatusClass(team.status)}" data-status="${escapeHtml(team.status)}">${escapeHtml(team.status)}</span></td>
        </tr>
      `).join('')
    : `<tr><td colspan="6" class="empty-state">No hay equipos cargados.</td></tr>`;

  const deliberationOrder = state.deliberationOrder || [];
  const deliberationList = deliberationOrder.length
    ? deliberationOrder.map((teamId, index) => {
        const team = rankingById.get(teamId);
        if (!team) return '';
        const qualifies = index < WINNER_CUTOFF;
        return `
          <div class="deliberation-card ${qualifies ? 'qualifies' : ''}" draggable="true" data-deliberation-index="${index}" data-team-id="${teamId}">
            <span class="deliberation-card__handle" title="Arrastra para reordenar">⠿⠿</span>
            <span class="deliberation-card__rank">#${index + 1}</span>
            <div class="deliberation-card__info">
              <strong>${escapeHtml(team.name)}${dayBadge(team)}</strong>
              <span class="muted">${formatNumber(team.average_5, 2)} / 5 · ${team.evaluation_count} evaluaciones</span>
            </div>
            <div class="deliberation-card__controls">
              <button type="button" class="button button--ghost" data-action="deliberation-move-up" data-deliberation-index="${index}" ${index === 0 ? 'disabled' : ''}>↑</button>
              <button type="button" class="button button--ghost" data-action="deliberation-move-down" data-deliberation-index="${index}" ${index === deliberationOrder.length - 1 ? 'disabled' : ''}>↓</button>
            </div>
          </div>
        `;
      }).join('')
    : '<div class="empty-state">No hay equipos cargados.</div>';

  const rubricAverages = selectedTeam
    ? state.rubrics.map((rubric) => `
        <article class="rubric-card">
          <h4>${escapeHtml(rubric.label)}</h4>
          <p>${escapeHtml(rubric.description)}</p>
          <div class="rubric-score">Promedio: ${formatNumber(selected?.team?.rubric_averages?.[rubric.key], 2)} / 5</div>
        </article>
      `).join('')
    : '<div class="empty-state">Seleccione un equipo para ver sus métricas.</div>';

  const jurorEvaluations = selectedEvaluations.length
    ? selectedEvaluations.map((evaluation) => `
        <article class="evaluation-item">
          <div class="evaluation-item__top">
            <div>
              <h4>${escapeHtml(evaluation.juror_name)}</h4>
              <small>${escapeHtml(evaluation.juror_identifier)}</small>
            </div>
            <span class="mini-pill" data-status="Evaluado">${formatNumber(evaluation.final_score_5, 2)} / 5</span>
          </div>
          <div class="mini-grid">
            <div class="meta-item"><span>Problema</span><strong>${evaluation.problem_score}</strong></div>
            <div class="meta-item"><span>Propuesta</span><strong>${evaluation.value_score}</strong></div>
            <div class="meta-item"><span>Validación</span><strong>${evaluation.validation_score}</strong></div>
            <div class="meta-item"><span>Negocio</span><strong>${evaluation.business_score}</strong></div>
          </div>
          <p><strong>Calidad del pitch:</strong> ${evaluation.pitch_score}</p>
          <p>${escapeHtml(evaluation.observations || 'Sin observaciones')}</p>
          <small>Creada: ${formatDate(evaluation.created_at)} · Actualizada: ${formatDate(evaluation.updated_at)}</small>
        </article>
      `).join('')
    : '<div class="empty-state">Todavía no existen evaluaciones para este equipo.</div>';


  const teamDetail = selectedTeam
    ? `
      <article class="detail-card">
        <div class="detail-header">
          <div>
            <p class="section-label">Detalle del equipo</p>
            <h3>${escapeHtml(selectedTeam.name)}</h3>
            <p class="muted">Posición actual #${selectedRanking} · ${selected?.team?.status || 'Pendiente'}</p>
          </div>
          <span class="status-pill" data-status="${escapeHtml(selected?.team?.status || 'Pendiente')}">${escapeHtml(selected?.team?.status || 'Pendiente')}</span>
        </div>
        <div class="detail-meta">
          <div class="meta-item"><span>Líder</span><strong>${escapeHtml(selectedTeam.leader)}</strong></div>
          <div class="meta-item"><span>País</span><strong>${escapeHtml(selectedTeam.country)}</strong></div>
          <div class="meta-item"><span>Universidad / filial</span><strong>${escapeHtml(getUniversityLabel(selectedTeam))}</strong></div>
          <div class="meta-item"><span>Línea temática</span><strong>${escapeHtml(selectedTeam.theme_line)}</strong></div>
          <div class="meta-item"><span>Promedio ponderado</span><strong>${formatNumber(selectedTeam.average_5, 2)} / 5</strong></div>
          <div class="meta-item"><span>Puntaje sobre 100</span><strong>${formatNumber(selectedTeam.average_100, 1)} / 100</strong></div>
          <div class="meta-item"><span>Evaluaciones</span><strong>${selectedTeam.evaluation_count}</strong></div>
          <div class="meta-item"><span>Origen</span><strong>Fila ${selectedTeam.source_row || '—'}</strong></div>
        </div>
        <div class="progress-shell">
          <div class="progress-label"><span>Estado consolidado</span><strong>${escapeHtml(selected?.juror_progress?.status || selectedTeam.status)}</strong></div>
          <div class="progress-line"><div class="progress-line__fill" style="width:${Math.min((selectedTeam.evaluation_count / Math.max(selected?.juror_progress?.total_jurors || 1, 1)) * 100, 100)}%"></div></div>
          <div class="progress-label"><span>${selected?.juror_progress?.evaluations_count || 0} evaluaciones registradas</span><strong>${selected?.juror_progress?.pending_count || 0} pendientes</strong></div>
        </div>
        <div class="stack">
          <div>
            <p class="section-label">Descripción del proyecto</p>
            <p class="muted" style="white-space: pre-wrap;">${richText(selectedTeam.description)}</p>
          </div>
          <div>
            <p class="section-label">Promedio por rúbrica</p>
            <div class="rubric-grid">${rubricAverages}</div>
          </div>
          <div>
            <p class="section-label">Evaluaciones jurado por jurado</p>
            <div class="evaluation-list">${jurorEvaluations}</div>
          </div>
        </div>
      </article>
    `
    : '<article class="detail-card"><div class="empty-state">Seleccione un equipo de la tabla para ver su detalle.</div></article>';

  const rankingHeaderActions = state.deliberating
    ? `
        <span class="badge">Arrastra las tarjetas para reordenar</span>
        <div style="display:flex; gap:8px;">
          <button class="button button--ghost" type="button" data-action="cancel-deliberation" ${state.deliberationSaving ? 'disabled' : ''}>Cancelar</button>
          <button class="button button--primary" type="button" data-action="save-deliberation" ${state.deliberationSaving ? 'disabled' : ''}>${state.deliberationSaving ? 'Guardando...' : 'Guardar orden'}</button>
        </div>
      `
    : `
        <span class="badge">Se recalcula tras cada evaluación</span>
        <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end;">
          <button class="button button--ghost" type="button" data-action="export-winners">Exportar ganadores (Excel)</button>
          <button class="button button--secondary" type="button" data-action="start-deliberation">Deliberar</button>
          ${state.user?.can_reset_evaluations ? '<button class="button button--danger" type="button" data-action="start-reset-evaluations">Reiniciar calificaciones</button>' : ''}
        </div>
      `;

  const winnersLegend = !state.deliberating
    ? `<div class="inline-feedback" style="margin-top:10px;"><span class="qualifies-swatch"></span> Los primeros ${WINNER_CUTOFF} lugares (resaltados) son los que clasifican.</div>
       <div class="inline-feedback" style="margin-top:6px;">${dayBadge({ schedule_day: '19 de agosto' })} ${dayBadge({ schedule_day: '20 de agosto' })} <span class="muted">indican el día en que presentó cada equipo — es solo informativo, es la misma competencia y no afecta la posición en el ranking.</span></div>`
    : '';

  const rankingBody = state.deliberating
    ? `<div class="deliberation-list">${deliberationList}</div>`
    : `
        <div style="overflow:auto;">
          <table class="ranking-table">
            <thead>
              <tr>
                <th>Posición</th>
                <th>Equipo</th>
                <th>Puntaje / 5</th>
                <th>Puntaje / 100</th>
                <th>Eval.</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody>${rankingRows}</tbody>
          </table>
        </div>
        ${winnersLegend}
      `;

  const confirmDeliberationModal = state.confirmingDeliberation
    ? `
      <div class="modal active" aria-hidden="false">
        <div class="modal__backdrop" data-action="cancel-confirm-deliberation"></div>
        <div class="modal__panel card" style="width:min(420px, 100%);">
          <div class="modal__header">
            <div>
              <p class="section-label">Confirmación</p>
              <h3>¿Estás seguro que quieres deliberar?</h3>
            </div>
          </div>
          <p class="muted">Recuerda que al guardar, la tabla de ranking quedará fija.</p>
          <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px;">
            <button class="button button--ghost" type="button" data-action="cancel-confirm-deliberation">Cancelar</button>
            <button class="button button--primary" type="button" data-action="confirm-start-deliberation">Sí, deliberar</button>
          </div>
        </div>
      </div>
    `
    : '';

  const confirmResetModal = state.confirmingResetEvaluations
    ? `
      <div class="modal active" aria-hidden="false">
        <div class="modal__backdrop" data-action="cancel-reset-evaluations"></div>
        <div class="modal__panel card" style="width:min(440px, 100%);">
          <div class="modal__header">
            <div>
              <p class="section-label">Acción irreversible</p>
              <h3>¿Reiniciar todas las calificaciones?</h3>
            </div>
          </div>
          <p class="muted">Se borrarán TODAS las evaluaciones, puntajes y observaciones de todos los jurados, y el ranking manual (deliberación) también se reiniciará. El sistema quedará como si ningún jurado hubiera votado. Esta acción no se puede deshacer.</p>
          <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px;">
            <button class="button button--ghost" type="button" data-action="cancel-reset-evaluations" ${state.resettingEvaluations ? 'disabled' : ''}>Cancelar</button>
            <button class="button button--danger" type="button" data-action="confirm-reset-evaluations" ${state.resettingEvaluations ? 'disabled' : ''}>${state.resettingEvaluations ? 'Reiniciando...' : 'Sí, borrar todo'}</button>
          </div>
        </div>
      </div>
    `
    : '';

  return `
    <div class="stack">
      ${summaryCards}
      <section class="split-grid">
        <article class="card">
          <div class="detail-header">
            <div>
              <p class="section-label">Ranking general</p>
              <h3>${state.deliberating ? 'Modo deliberación: arrastra para reordenar' : 'Posiciones actualizadas automáticamente'}</h3>
            </div>
            <div class="detail-header__actions">
              ${rankingHeaderActions}
            </div>
          </div>
          ${rankingBody}
        </article>
        <div class="detail-layout">
          ${teamDetail}
        </div>
      </section>
    </div>
    ${confirmDeliberationModal}
    ${confirmResetModal}
  `;
}

const VALID_SCORE_VALUES = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5];

function renderRatingButtons(rubricKey, currentValue) {
  return VALID_SCORE_VALUES.map((value) => `
    <button
      type="button"
      class="rating-button ${Number(currentValue) === value ? 'active' : ''}"
      data-action="set-rating"
      data-rubric="${rubricKey}"
      data-value="${value}"
    >
      ${value.toFixed(1)}
    </button>
  `).join('');
}

function renderJuryDashboard() {
  const dashboard = state.dashboard || {};
  const currentTeam = dashboard.current_team;
  const currentDetail = dashboard.current_team_detail;
  const evaluation = dashboard.current_evaluation;
  const teams = dashboard.teams || [];
  const progress = dashboard.progress || { assigned: 0, completed: 0, pending: 0, percent: 0 };
  const totalTeams = progress.assigned || teams.length || 0;
  const currentIndex = (dashboard.current_index || 0) + 1;
  const currentSummary = currentDetail?.team || currentTeam;
  const currentStatus = currentSummary?.status || 'Pendiente';
  const currentScore = currentSummary?.average_5 || 0;

  if (!state.juryDraft) {
    state.juryDraft = buildDraftFromEvaluation(evaluation);
  }

  const currentDraft = state.juryDraft;
  const teamInfo = currentSummary
    ? `
      <article class="team-card">
        <p class="section-label">Equipo actual</p>
        <h3>${escapeHtml(currentSummary.name)}${dayBadge(currentSummary)}</h3>
        <p class="muted">${escapeHtml(currentSummary.description)}</p>
        <div class="team-meta-grid">
          <div class="team-info-item"><span>Líder</span><strong>${escapeHtml(currentSummary.leader)}</strong></div>
          <div class="team-info-item"><span>País</span><strong>${escapeHtml(currentSummary.country)}</strong></div>
          <div class="team-info-item"><span>Universidad / filial</span><strong>${escapeHtml(getUniversityLabel(currentSummary))}</strong></div>
          <div class="team-info-item"><span>Línea temática</span><strong>${escapeHtml(currentSummary.theme_line)}</strong></div>
        </div>
      </article>
    `
    : '<article class="team-card"><div class="empty-state">No hay equipos disponibles.</div></article>';

  const progressCard = `
    <aside class="progress-card">
      <p class="section-label">Progreso</p>
      <h3>Equipo ${currentIndex} de ${totalTeams || 0}</h3>
      <p class="muted">${escapeHtml(teamSummaryText(currentSummary))}</p>
      <div class="progress-shell">
        <div class="progress-label"><span>Avance personal</span><strong>${progress.completed}/${progress.assigned} equipos</strong></div>
        <div class="progress-line"><div class="progress-line__fill" style="width:${progress.percent || 0}%"></div></div>
        <div class="progress-label"><span>${progress.percent || 0}% completado</span><strong><span class="status-pill" data-status="${escapeHtml(currentStatus)}">${escapeHtml(currentStatus)}</span></strong></div>
      </div>
      <div class="team-info-item" style="margin-top:14px;">
        <span>Puntaje promedio del equipo</span>
        <strong>${formatNumber(currentScore, 2)} / 5</strong>
      </div>
      <div class="team-info-item" style="margin-top:10px;">
        <span>Puntaje sobre 100</span>
        <strong>${formatNumber(currentSummary?.average_100, 1)} / 100</strong>
      </div>
      <div class="jury-meta">
        <span class="mini-pill" data-status="${escapeHtml(currentStatus)}">${escapeHtml(currentStatus)}</span>
        <span class="mini-pill">${currentDetail?.juror_progress?.evaluations_count || 0} evaluaciones</span>
      </div>
    </aside>
  `;

  const rubricCards = state.rubrics.map((rubric) => `
    <article class="rubric-card">
      <div class="rubric-shell-header">
        <div>
          <h3>${escapeHtml(rubric.label)}</h3>
          <p>${escapeHtml(rubric.description)}</p>
        </div>
        <span class="badge">${Math.round(rubric.weight * 100)}%</span>
      </div>
      <div class="rating-row">
        <div>${renderRatingButtons(rubric.key, currentDraft.scores[rubric.key])}</div>
        <div class="rubric-score">${currentDraft.scores[rubric.key] || '—'}/5</div>
      </div>
    </article>
  `).join('');

  const currentEvaluationCard = evaluation
    ? `
      <div class="inline-feedback">
        Guardado el ${formatDate(evaluation.updated_at)} · Puntaje final: ${formatNumber(evaluation.final_score_5, 2)} / 5 · ${formatNumber(evaluation.final_score_100, 1)} / 100
      </div>
    `
    : '<div class="inline-feedback">Aún no existe una evaluación guardada para este equipo.</div>';

  const navigationLabel = state.juryDirty
    ? 'Hay cambios sin guardar'
    : 'Lista para avanzar';

  const saveDisabled = !isDraftComplete(currentDraft);
  const rubricAlreadyScored = scoreFields.every((key) => isValidScoreValue(currentDraft?.scores?.[key]));
  const aiUsed = Boolean(dashboard.ai_improvement_used);
  const hasObservationsText = Boolean((currentDraft.observations || '').trim());
  const aiOriginalText = dashboard.current_team_id !== undefined && dashboard.current_team_id !== null
    ? state.aiOriginalByTeam[dashboard.current_team_id]
    : undefined;

  return `
    <div class="jury-header stack">
      <section class="team-hero">
        ${teamInfo}
        ${progressCard}
      </section>

      <section class="rubric-slab">
        <div class="detail-header">
          <div>
            <p class="section-label">Evaluación</p>
            <h3>Califica una rúbrica a la vez</h3>
          </div>
          <span class="badge">${navigationLabel}</span>
        </div>
        <div class="rubric-grid">
          ${rubricCards}
        </div>
      </section>

      <section class="observations-card">
        <p class="section-label">Observaciones</p>
        <h3>Comentarios para el equipo <span class="required-marker">*</span></h3>
        <textarea id="jury-observations" placeholder="Escriba aquí sus observaciones (obligatorio)..." required>${escapeHtml(currentDraft.observations || '')}</textarea>
        <div class="ai-actions">
          ${!aiUsed
            ? `<button type="button" class="button button--ghost" data-action="ai-improve" ${state.aiImproving || !hasObservationsText ? 'disabled' : ''}>${state.aiImproving ? 'Mejorando...' : '✨ Mejorar con IA'}</button>`
            : `<span class="ai-used-note">✨ Ya usaste tu mejora con IA para este equipo</span>`}
          ${aiOriginalText !== undefined ? `<button type="button" class="button button--ghost" data-action="ai-revert">↩ Volver a texto original</button>` : ''}
        </div>
        ${currentEvaluationCard}
      </section>

      <section class="navigation-card">
        <p class="section-label">Navegación</p>
        <h3>Flujo secuencial</h3>
        <p class="navigation-card__team">Equipo actual: <strong>${escapeHtml(currentSummary?.name || '—')}</strong></p>
        <div class="navigation-actions">
          <button class="button button--danger" type="button" data-action="jury-no-show" ${rubricAlreadyScored ? 'disabled title="Ya calificaste este equipo, no puedes marcarlo como no asistió."' : ''}>No asistió</button>
          <button class="button button--primary" type="button" data-action="jury-save" ${saveDisabled ? 'disabled' : ''}>Guardar evaluación</button>
        </div>
        <div class="inline-feedback">${state.juryDirty ? 'Tienes cambios sin guardar.' : 'Al guardar avanzas automáticamente al siguiente equipo pendiente. Si el equipo no se presentó, usa "No asistió" para pasarlo al final de la lista.'}</div>
      </section>
    </div>
  `;
}

function openModal() {
  state.modalOpen = true;
  dom.teamModal.classList.add('active');
  dom.teamModal.setAttribute('aria-hidden', 'false');
  dom.teamSearch.value = state.teamSearch || '';
  renderTeamSearchResults();
  requestAnimationFrame(() => dom.teamSearch.focus());
}

function closeModal() {
  state.modalOpen = false;
  dom.teamModal.classList.remove('active');
  dom.teamModal.setAttribute('aria-hidden', 'true');
}

function renderTeamSearchResults() {
  const query = (dom.teamSearch.value || '').trim().toLowerCase();
  state.teamSearch = query;
  const teams = state.dashboard?.teams || [];
  const filtered = teams.filter((team) => {
    if (!query) return true;
    return [team.name, team.country, team.university, team.theme_line, team.leader]
      .some((value) => String(value || '').toLowerCase().includes(query));
  });

  if (!filtered.length) {
    dom.teamSearchResults.innerHTML = '<div class="empty-state">No se encontraron equipos.</div>';
    return;
  }

  dom.teamSearchResults.innerHTML = filtered.map((team) => `
    <button type="button" class="search-item" data-action="choose-team" data-team-id="${team.id}">
      <div class="search-item__top">
        <div>
          <h4>${escapeHtml(team.name)}${dayBadge(team)}</h4>
          <small>Orden ${team.display_order} · ${escapeHtml(team.status)}</small>
        </div>
        <span class="mini-pill" data-status="${escapeHtml(team.status)}">#${team.position || '—'}</span>
      </div>
      <p>${escapeHtml(getUniversityLabel(team))}</p>
    </button>
  `).join('');
}

function confirmDiscardChanges() {
  if (!state.juryDirty) return true;
  return window.confirm('Tienes cambios sin guardar. Si continúas, se perderán los cambios actuales.');
}

async function loadDashboard(teamId = null) {
  if (!state.authenticated || !state.user) return;
  const role = state.user.role;
  const endpoint = role === 'admin'
    ? `/api/admin/dashboard${teamId ? `?team_id=${teamId}` : ''}`
    : `/api/jury/dashboard${teamId ? `?team_id=${teamId}` : ''}`;

  state.loading = true;
  try {
    const payload = await api(endpoint);
    state.dashboard = payload.dashboard;
    state.activeTeamId = role === 'admin'
      ? payload.dashboard?.selected_team?.team?.id || teamId || state.dashboard?.ranking?.[0]?.id || null
      : payload.dashboard?.current_team_id || teamId || state.dashboard?.current_team_id || null;
    state.juryDraft = role === 'jury' ? buildDraftFromEvaluation(payload.dashboard?.current_evaluation) : null;
    state.juryDirty = false;
    render();
    if (state.modalOpen) renderTeamSearchResults();
  } catch (error) {
    if (error.status === 401) {
      await forceLogout(false);
      setToast('La sesión expiró. Vuelve a iniciar sesión.', 'error');
      return;
    }
    setToast(error.message, 'error');
  } finally {
    state.loading = false;
  }
}

async function loadBootstrap() {
  try {
    const payload = await api('/api/bootstrap');
    syncSessionState(payload);
    render();
    if (state.authenticated) {
      if (state.role === 'jury' && !state.dashboard?.current_evaluation) {
        state.juryDraft = buildDraftFromEvaluation(null);
      }
      if (state.role === 'jury') {
        state.juryDirty = false;
      }
    }
  } catch (error) {
    setToast(error.message, 'error');
    setScreen(false);
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const identifier = dom.loginIdentifier.value.trim();
  const password = dom.loginPassword.value;
  if (!identifier || !password) return;

  dom.loginFeedback.textContent = 'Validando credenciales...';
  try {
    const payload = await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({ identifier, password }),
    });
    dom.loginFeedback.textContent = 'Inicio de sesión correcto.';
    await loadBootstrap();
    setToast(`Bienvenido, ${payload.user.name}.`);
  } catch (error) {
    dom.loginFeedback.textContent = error.message;
    setToast(error.message, 'error');
  }
}

async function forceLogout(showMessage = true) {
  try {
    await api('/api/logout', { method: 'POST', body: '{}' });
  } catch (error) {
    // Ignore logout network errors and reset local state anyway.
  }
  state.authenticated = false;
  state.user = null;
  state.rubrics = [];
  state.dashboard = null;
  state.role = null;
  state.activeTeamId = null;
  state.juryDraft = null;
  state.juryDirty = false;
  closeModal();
  render();
  if (showMessage) {
    setToast('Sesión cerrada.', 'info');
  }
}

async function handleSaveEvaluation() {
  if (state.role !== 'jury' || !state.dashboard?.current_team_id) return;
  const scoresOk = scoreFields.every((key) => isValidScoreValue(state.juryDraft?.scores?.[key]));
  const observationsOk = Boolean((state.juryDraft?.observations || '').trim());
  if (!scoresOk) {
    setToast('Completa las cinco rúbricas antes de guardar.', 'error');
    return;
  }
  if (!observationsOk) {
    setToast('Escribe una observación antes de guardar.', 'error');
    return;
  }

  const teamId = state.dashboard.current_team_id;
  try {
    const payload = {
      problem_score: Number(state.juryDraft.scores.problem_score),
      value_score: Number(state.juryDraft.scores.value_score),
      validation_score: Number(state.juryDraft.scores.validation_score),
      business_score: Number(state.juryDraft.scores.business_score),
      pitch_score: Number(state.juryDraft.scores.pitch_score),
      observations: state.juryDraft.observations || '',
    };
    const response = await api(`/api/jury/evaluation/${teamId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    state.dashboard = response.dashboard;
    state.juryDraft = buildDraftFromEvaluation(response.dashboard.current_evaluation);
    state.juryDirty = false;
    state.activeTeamId = response.dashboard.current_team_id;
    render();
    scrollDashboardToTop();
    setToast(response.message || 'Evaluación guardada correctamente.');
  } catch (error) {
    setToast(error.message, 'error');
  }
}

async function handleAiImprove() {
  if (state.role !== 'jury' || !state.dashboard?.current_team_id) return;
  if (state.dashboard.ai_improvement_used || state.aiImproving) return;

  const teamId = state.dashboard.current_team_id;
  const text = (state.juryDraft?.observations || '').trim();
  if (!text) {
    setToast('Escribe una observación antes de mejorarla con IA.', 'error');
    return;
  }

  state.aiImproving = true;
  render();
  try {
    const response = await api(`/api/jury/team/${teamId}/improve-observations`, {
      method: 'POST',
      body: JSON.stringify({ texto_original: text }),
    });
    state.aiOriginalByTeam[teamId] = text;
    state.juryDraft.observations = response.improved_text;
    state.juryDirty = true;
    state.dashboard.ai_improvement_used = true;
    setToast('Observación mejorada con IA. Puedes editarla o volver al texto original.');
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.aiImproving = false;
    render();
  }
}

function handleAiRevert() {
  const teamId = state.dashboard?.current_team_id;
  if (teamId === undefined || teamId === null) return;
  const original = state.aiOriginalByTeam[teamId];
  if (original === undefined) return;
  state.juryDraft.observations = original;
  state.juryDirty = true;
  delete state.aiOriginalByTeam[teamId];
  render();
}

async function handleJuryNoShow() {
  if (state.role !== 'jury' || !state.dashboard?.current_team_id) return;
  const rubricAlreadyScored = scoreFields.every((key) => isValidScoreValue(state.juryDraft?.scores?.[key]));
  if (rubricAlreadyScored) {
    setToast('Ya calificaste este equipo, no puedes marcarlo como no asistió.', 'error');
    return;
  }
  if (!confirmDiscardChanges()) return;

  const teamId = state.dashboard.current_team_id;
  try {
    const response = await api(`/api/jury/team/${teamId}/no-show`, {
      method: 'POST',
      body: '{}',
    });
    state.dashboard = response.dashboard;
    state.juryDraft = buildDraftFromEvaluation(response.dashboard.current_evaluation);
    state.juryDirty = false;
    state.activeTeamId = response.dashboard.current_team_id;
    render();
    scrollDashboardToTop();
    setToast(response.message || "Equipo marcado como 'No asistió'.");
  } catch (error) {
    setToast(error.message, 'error');
  }
}

async function navigateTeam(offsetOrId) {
  if (state.role !== 'jury') return;
  if (!confirmDiscardChanges()) return;

  const teams = state.dashboard?.teams || [];
  const currentIndex = state.dashboard?.current_index ?? 0;
  let targetId = null;

  if (typeof offsetOrId === 'number') {
    const targetIndex = currentIndex + offsetOrId;
    if (targetIndex < 0 || targetIndex >= teams.length) return;
    targetId = teams[targetIndex]?.id || null;
  } else {
    targetId = offsetOrId;
  }

  if (!targetId) return;
  await loadDashboard(targetId);
}

async function handleRating(event) {
  const button = event.target.closest('[data-action="set-rating"]');
  if (!button) return;
  event.preventDefault();
  const rubricKey = button.dataset.rubric;
  const value = Number(button.dataset.value);
  if (!scoreFields.includes(rubricKey) || !VALID_SCORE_VALUES.includes(value)) return;
  state.juryDraft = state.juryDraft || buildDraftFromEvaluation(null);
  state.juryDraft.scores[rubricKey] = value;
  state.juryDirty = true;
  render();
}

function handleRootClick(event) {
  const actionElement = event.target.closest('[data-action]');
  if (!actionElement) return;
  const action = actionElement.dataset.action;

  if (action === 'select-admin-team') {
    const teamId = actionElement.dataset.teamId;
    if (teamId) loadDashboard(teamId);
    return;
  }

  if (action === 'export-all-pdf') {
    exportAllTeamsObservationsPdf();
    return;
  }

  if (action === 'export-winners') {
    exportWinnersExcel();
    return;
  }

  if (action === 'start-deliberation') {
    startDeliberation();
    return;
  }

  if (action === 'confirm-start-deliberation') {
    confirmStartDeliberation();
    return;
  }

  if (action === 'cancel-confirm-deliberation') {
    cancelConfirmDeliberation();
    return;
  }

  if (action === 'start-reset-evaluations') {
    startResetEvaluations();
    return;
  }

  if (action === 'cancel-reset-evaluations') {
    cancelResetEvaluations();
    return;
  }

  if (action === 'confirm-reset-evaluations') {
    confirmResetEvaluations();
    return;
  }

  if (action === 'cancel-deliberation') {
    cancelDeliberation();
    return;
  }

  if (action === 'save-deliberation') {
    saveDeliberation();
    return;
  }

  if (action === 'deliberation-move-up' || action === 'deliberation-move-down') {
    const index = Number(actionElement.dataset.deliberationIndex);
    const targetIndex = action === 'deliberation-move-up' ? index - 1 : index + 1;
    reorderDeliberation(index, targetIndex);
    return;
  }

  if (action === 'jury-no-show') {
    handleJuryNoShow();
    return;
  }

  if (action === 'ai-improve') {
    handleAiImprove();
    return;
  }

  if (action === 'ai-revert') {
    handleAiRevert();
    return;
  }

  if (action === 'jury-save') {
    handleSaveEvaluation();
    return;
  }

  if (action === 'open-team-selector') {
    if (!confirmDiscardChanges()) return;
    openModal();
    return;
  }

  if (action === 'choose-team') {
    const teamId = actionElement.dataset.teamId;
    closeModal();
    if (teamId) loadDashboard(teamId);
    return;
  }

  if (action === 'close-modal') {
    closeModal();
    return;
  }
}

function handleRootInput(event) {
  const target = event.target;
  if (target?.id === 'jury-observations') {
    state.juryDraft = state.juryDraft || buildDraftFromEvaluation(null);
    state.juryDraft.observations = target.value;
    state.juryDirty = true;
    const saveButton = document.querySelector('[data-action="jury-save"]');
    if (saveButton) {
      saveButton.disabled = !isDraftComplete(state.juryDraft);
    }
    const aiButton = document.querySelector('[data-action="ai-improve"]');
    if (aiButton) {
      aiButton.disabled = state.aiImproving || !target.value.trim();
    }
    return;
  }

  if (target?.id === 'team-search') {
    renderTeamSearchResults();
  }
}

async function handleRefresh() {
  if (!state.authenticated) {
    await loadBootstrap();
    return;
  }
  await loadDashboard(state.activeTeamId);
}

async function handleLogout() {
  await forceLogout(true);
  setLoginFeedback('');
}

function bindEvents() {
  dom.loginForm.addEventListener('submit', handleLogin);
  dom.togglePassword.addEventListener('click', () => {
    const isHidden = dom.loginPassword.type === 'password';
    dom.loginPassword.type = isHidden ? 'text' : 'password';
    dom.togglePassword.textContent = isHidden ? '🙈' : '👁';
    dom.togglePassword.setAttribute('aria-pressed', String(isHidden));
    dom.togglePassword.setAttribute('aria-label', isHidden ? 'Ocultar contraseña' : 'Mostrar contraseña');
  });
  dom.dashboardRoot.addEventListener('click', (event) => {
    handleRating(event);
    handleRootClick(event);
  });
  dom.dashboardRoot.addEventListener('input', handleRootInput);
  dom.dashboardRoot.addEventListener('dragstart', handleDeliberationDragStart);
  dom.dashboardRoot.addEventListener('dragover', handleDeliberationDragOver);
  dom.dashboardRoot.addEventListener('drop', handleDeliberationDrop);
  dom.dashboardRoot.addEventListener('dragend', handleDeliberationDragEnd);
  dom.logoutButton.addEventListener('click', handleLogout);
  dom.refreshButton.addEventListener('click', handleRefresh);
  dom.teamModal.addEventListener('click', handleRootClick);
  dom.teamSearch.addEventListener('input', renderTeamSearchResults);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.modalOpen) {
      closeModal();
    }
  });
  window.addEventListener('beforeunload', (event) => {
    if (state.juryDirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
}

async function init() {
  bindEvents();
  await loadBootstrap();
  if (!state.authenticated) {
    setScreen(false);
  }
}

init();