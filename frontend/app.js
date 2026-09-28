const state = {
  authenticated: false,
  user: null,
  rubrics: [],
  dashboard: null,
  role: null,
  adminPitch: null,
  activatingPitch: null,
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
  presentationEditing: false,
  presentationOrder: null,
  presentationSaving: false,
  presentationBusyTeamId: null,
  jurorBusyId: null,
  confirmingDeactivateJurorId: null,
  confirmingDeactivateTeamId: null,
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

function startDeliberation() {
  if (state.adminPitch !== 1) return;
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
    const response = await fetch(`/api/admin/export-winners?pitch=${state.adminPitch || 1}`, {
      credentials: 'same-origin',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'No se pudo generar el Excel de ganadores.');
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : `ganadores_pitch${state.adminPitch || 1}.xlsx`;
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
    const response = await fetch(`/api/admin/export-pdf?pitch=${state.adminPitch || 1}`, {
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
  state.adminPitch = state.role === 'admin' ? null : state.adminPitch;
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
    dom.dashboardRoot.innerHTML = !state.adminPitch
      ? renderAdminPitchSelector()
      : `${renderAdminPitchBar()}${state.adminPitch === 3
        ? '<div class="empty-state">Coming soon</div>'
        : renderAdminDashboard()}`;
  } else {
    dom.dashboardRoot.innerHTML = renderJuryDashboard();
  }
}

const ADMIN_PITCHES = [1, 2, 3];

function pitchLabel(n) {
  return `2026 - ${n} Pitch`;
}

function renderAdminPitchSelector() {
  const activePitch = state.dashboard?.active_pitch || 1;
  const activatingThis = (n) => state.activatingPitch === n;

  const pitchTiles = ADMIN_PITCHES.map((n) => {
    const isLive = activePitch === n;
    const isSoon = n === 3;
    const status = isLive
      ? '<span class="live-pill"><span class="live-dot"></span>En vivo para jurados</span>'
      : isSoon ? '<span class="soon-pill">Próximamente</span>' : '';
    return `
      <button class="pitch-tile ${isLive ? 'pitch-tile--live' : ''}" type="button" data-action="select-admin-pitch" data-pitch="${n}">
        <span class="pitch-tile__top">
          <span class="pitch-tile__number">${n}</span>
          ${status}
        </span>
        <span class="pitch-tile__title">${pitchLabel(n)}</span>
        <span class="pitch-tile__cta">Ver ranking y evaluaciones <span aria-hidden="true">→</span></span>
      </button>
    `;
  }).join('');

  const segments = ADMIN_PITCHES.map((n) => {
    const isLive = activePitch === n;
    const text = activatingThis(n) ? 'Activando...' : isLive ? 'Activo' : 'Activar';
    return `
      <button
        class="segment ${isLive ? 'segment--active' : ''}"
        type="button"
        role="radio"
        aria-checked="${isLive}"
        data-action="activate-pitch"
        data-pitch="${n}"
        ${isLive || state.activatingPitch ? 'disabled' : ''}
      >
        <span class="segment__title">${pitchLabel(n)}</span>
        <span class="segment__state">${isLive ? '<span class="live-dot"></span>' : ''}${text}</span>
      </button>
    `;
  }).join('');

  return `
    <div class="stack">
      <section class="card">
        <div class="detail-header">
          <div>
            <p class="section-label">Seleccionar competencia</p>
            <h3>Elige un Pitch</h3>
            <p class="muted">Consulta el ranking y las evaluaciones de cada Pitch.</p>
          </div>
        </div>
        <div class="pitch-tiles">${pitchTiles}</div>
      </section>

      <section class="card jury-control">
        <div class="jury-control__header">
          <div>
            <p class="section-label">Control para jurados</p>
            <h3>¿Qué Pitch califican los jurados?</h3>
            <p class="muted">Define el Pitch que ven y pueden calificar los jurados ahora mismo, sin importar cuál estés consultando tú.</p>
          </div>
          <div class="jury-control__now">
            <span class="jury-control__now-label">En vivo ahora</span>
            <span class="live-pill live-pill--lg"><span class="live-dot"></span>${pitchLabel(activePitch)}</span>
          </div>
        </div>
        <div class="segmented" role="radiogroup" aria-label="Pitch activo para jurados">${segments}</div>
      </section>

      ${renderJurorsCard()}
    </div>
    ${renderConfirmDeactivateJurorModal()}
  `;
}

function jurorInitials(name) {
  return String(name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('');
}

function renderJurorsCard() {
  const jurors = state.dashboard?.jurors || [];
  const activeCount = jurors.filter((juror) => juror.active).length;
  const anyBusy = state.jurorBusyId !== null;
  const rows = jurors.map((juror) => {
    const counts = juror.evaluations_by_pitch || {};
    const busy = state.jurorBusyId === juror.id;
    const action = juror.active
      ? `<button type="button" class="button button--danger-ghost button--sm" data-action="juror-deactivate" data-juror-id="${juror.id}" ${anyBusy ? 'disabled' : ''}>${busy ? 'Desactivando...' : 'Desactivar'}</button>`
      : `<button type="button" class="button button--secondary button--sm" data-action="juror-activate" data-juror-id="${juror.id}" ${anyBusy ? 'disabled' : ''}>${busy ? 'Activando...' : 'Activar'}</button>`;
    return `
      <div class="juror-row ${juror.active ? '' : 'juror-row--inactive'}">
        <span class="juror-row__avatar" aria-hidden="true">${escapeHtml(jurorInitials(juror.name))}</span>
        <div class="juror-row__info">
          <strong>${escapeHtml(juror.name)}</strong>
          <span class="muted">${escapeHtml(juror.identifier)}</span>
        </div>
        <span class="juror-row__counts" title="Evaluaciones guardadas por Pitch">Pitch 1: ${counts['1'] || 0} · Pitch 2: ${counts['2'] || 0}</span>
        <span class="juror-status ${juror.active ? 'juror-status--active' : ''}">${juror.active ? 'Activo' : 'Inactivo'}</span>
        <div class="juror-row__actions">${action}</div>
      </div>
    `;
  }).join('');

  return `
    <section class="card">
      <div class="detail-header">
        <div>
          <p class="section-label">Jurados</p>
          <h3>Jurados habilitados para calificar</h3>
          <p class="muted">Si un jurado no asiste, desactívalo: no podrá iniciar sesión y los equipos dejarán de quedar "En progreso" esperando su calificación. Las calificaciones que ya guardó se conservan.</p>
        </div>
        <span class="badge" style="white-space:nowrap;">${activeCount} de ${jurors.length} activos</span>
      </div>
      <div class="juror-list">${rows || '<div class="empty-state">No hay jurados registrados.</div>'}</div>
    </section>
  `;
}

function renderConfirmDeactivateJurorModal() {
  if (state.confirmingDeactivateJurorId === null) return '';
  const juror = (state.dashboard?.jurors || []).find((item) => item.id === state.confirmingDeactivateJurorId);
  return `
    <div class="modal active" aria-hidden="false">
      <div class="modal__backdrop" data-action="juror-cancel-deactivate"></div>
      <div class="modal__panel card" style="width:min(440px, 100%);">
        <div class="modal__header">
          <div>
            <p class="section-label">Confirmación</p>
            <h3>¿Desactivar a ${escapeHtml(juror?.name || 'este jurado')}?</h3>
          </div>
        </div>
        <p class="muted">Se cerrará su sesión y no podrá calificar hasta que lo actives de nuevo. Los equipos ya no esperarán su calificación para quedar como "Evaluado". Sus calificaciones guardadas se conservan.</p>
        <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px;">
          <button class="button button--ghost" type="button" data-action="juror-cancel-deactivate">Cancelar</button>
          <button class="button button--danger" type="button" data-action="juror-confirm-deactivate">Sí, desactivar</button>
        </div>
      </div>
    </div>
  `;
}

async function setJurorActive(jurorId, active) {
  if (state.jurorBusyId !== null) return;
  state.jurorBusyId = jurorId;
  state.confirmingDeactivateJurorId = null;
  render();
  try {
    const response = await api(`/api/admin/jurors/${jurorId}/${active ? 'activate' : 'deactivate'}`, {
      method: 'POST',
      body: '{}',
    });
    setToast(response.message || 'Cambio guardado.');
    await loadBootstrap();
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.jurorBusyId = null;
    render();
  }
}

function renderAdminPitchBar() {
  const activePitch = state.dashboard?.active_pitch || 1;
  const isLive = state.adminPitch === activePitch;
  return `
    <div class="pitch-bar card">
      <button class="button button--ghost" type="button" data-action="back-to-pitch-selector"><span aria-hidden="true">←</span> Todos los Pitch</button>
      <div class="pitch-bar__info">
        <span class="pitch-bar__label">Estás viendo</span>
        <strong>${pitchLabel(state.adminPitch)}</strong>
      </div>
      ${isLive
        ? '<span class="live-pill"><span class="live-dot"></span>En vivo para jurados</span>'
        : `<span class="muted pitch-bar__note">Los jurados califican: ${pitchLabel(activePitch)}</span>`}
    </div>
  `;
}

function getAdminPitchDashboard() {
  return state.dashboard || {};
}

function renderAdminDashboard() {
  const dashboard = getAdminPitchDashboard();
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

  // Pitch 1: clasifican 20 al Pitch 2. Pitch 2: salen 10 finalistas.
  const WINNER_CUTOFF = dashboard.winners_count || (state.adminPitch === 2 ? 10 : 20);
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
          ${state.adminPitch === 1 ? '<button class="button button--secondary" type="button" data-action="start-deliberation">Deliberar</button>' : ''}
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

  return `
    <div class="stack">
      ${summaryCards}
      ${renderPresentationCard(dashboard)}
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
    ${renderConfirmDeactivateModal(dashboard)}
  `;
}

function renderPresentationInfo(team) {
  return `
    <div class="presentation-row__info">
      <strong>${escapeHtml(team.name)}${dayBadge(team)}</strong>
      ${team.schedule_time ? `<span class="muted">${escapeHtml(team.schedule_time)}</span>` : ''}
    </div>
  `;
}

function renderPresentationCard(dashboard) {
  const items = dashboard.presentation || [];
  const byId = Object.fromEntries(items.map((team) => [team.id, team]));
  const active = items.filter((team) => !team.deactivated);
  const inactive = items.filter((team) => team.deactivated);
  const jurorCount = dashboard.summary?.jurors || 0;
  const busy = (teamId) => state.presentationBusyTeamId === teamId;
  const anyBusy = state.presentationBusyTeamId !== null;

  let list;
  if (state.presentationEditing) {
    const order = (state.presentationOrder || []).filter((id) => byId[id]);
    list = order.map((teamId, index) => `
      <div class="presentation-row presentation-row--editing" draggable="true" data-presentation-index="${index}">
        <span class="deliberation-card__handle" title="Arrastra para reordenar">⠿⠿</span>
        <span class="presentation-row__pos">${index + 1}</span>
        ${renderPresentationInfo(byId[teamId])}
        <div class="presentation-row__actions">
          <button type="button" class="button button--ghost button--sm" data-action="presentation-move-up" data-presentation-index="${index}" ${index === 0 ? 'disabled' : ''} aria-label="Subir">↑</button>
          <button type="button" class="button button--ghost button--sm" data-action="presentation-move-down" data-presentation-index="${index}" ${index === order.length - 1 ? 'disabled' : ''} aria-label="Bajar">↓</button>
        </div>
      </div>
    `).join('');
  } else {
    list = active.map((team, index) => `
      <div class="presentation-row">
        <span class="presentation-row__pos">${index + 1}</span>
        ${renderPresentationInfo(team)}
        <span class="status-pill" title="Evaluaciones de jurados activos">${team.active_evaluation_count ?? team.evaluation_count}/${jurorCount} eval.</span>
        <div class="presentation-row__actions">
          <button type="button" class="button button--ghost button--sm" data-action="presentation-move-end" data-team-id="${team.id}" ${index === active.length - 1 || anyBusy ? 'disabled' : ''}>${busy(team.id) ? 'Moviendo...' : 'Mover al final'}</button>
          <button type="button" class="button button--danger-ghost button--sm" data-action="presentation-deactivate" data-team-id="${team.id}" ${anyBusy ? 'disabled' : ''}>Desactivar</button>
        </div>
      </div>
    `).join('');
  }

  const inactiveList = inactive.length && !state.presentationEditing
    ? `
      <div class="presentation-inactive">
        <p class="presentation-inactive__title">Desactivados (${inactive.length}) · solo los ven los administradores</p>
        ${inactive.map((team) => `
          <div class="presentation-row presentation-row--inactive">
            <span class="presentation-row__pos">—</span>
            ${renderPresentationInfo(team)}
            <span class="status-pill">${team.active_evaluation_count ?? team.evaluation_count}/${jurorCount} eval.</span>
            <div class="presentation-row__actions">
              <button type="button" class="button button--secondary button--sm" data-action="presentation-reactivate" data-team-id="${team.id}" ${anyBusy ? 'disabled' : ''}>${busy(team.id) ? 'Reactivando...' : 'Reactivar'}</button>
            </div>
          </div>
        `).join('')}
      </div>
    `
    : '';

  const actions = state.presentationEditing
    ? `
      <button class="button button--ghost" type="button" data-action="presentation-cancel" ${state.presentationSaving ? 'disabled' : ''}>Cancelar</button>
      <button class="button button--primary" type="button" data-action="presentation-save" ${state.presentationSaving ? 'disabled' : ''}>${state.presentationSaving ? 'Guardando...' : 'Guardar orden'}</button>
    `
    : `<button class="button button--secondary" type="button" data-action="presentation-edit" ${active.length < 2 || anyBusy ? 'disabled' : ''}>Reordenar</button>`;

  return `
    <article class="card presentation-card">
      <div class="detail-header">
        <div>
          <p class="section-label">Orden de presentación</p>
          <h3>${state.presentationEditing ? 'Arrastra o usa las flechas para reordenar' : 'Orden en que los jurados califican'}</h3>
          <p class="muted">Si un equipo no se presentó, muévelo al final o desactívalo para que los jurados no lo vean más.</p>
        </div>
        <div class="detail-header__actions">${actions}</div>
      </div>
      <div class="presentation-list">${list || '<div class="empty-state">No hay equipos activos en este Pitch.</div>'}</div>
      ${inactiveList}
    </article>
  `;
}

function renderConfirmDeactivateModal(dashboard) {
  if (state.confirmingDeactivateTeamId === null) return '';
  const team = (dashboard.presentation || []).find((item) => item.id === state.confirmingDeactivateTeamId);
  return `
    <div class="modal active" aria-hidden="false">
      <div class="modal__backdrop" data-action="presentation-cancel-deactivate"></div>
      <div class="modal__panel card" style="width:min(440px, 100%);">
        <div class="modal__header">
          <div>
            <p class="section-label">Confirmación</p>
            <h3>¿Desactivar a ${escapeHtml(team?.name || 'este equipo')}?</h3>
          </div>
        </div>
        <p class="muted">Los jurados dejarán de verlo en ${pitchLabel(state.adminPitch)} y no podrán calificarlo. Las evaluaciones que ya tenga se conservan y puedes reactivarlo cuando quieras.</p>
        <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px;">
          <button class="button button--ghost" type="button" data-action="presentation-cancel-deactivate">Cancelar</button>
          <button class="button button--danger" type="button" data-action="presentation-confirm-deactivate">Sí, desactivar</button>
        </div>
      </div>
    </div>
  `;
}

function startPresentationEdit() {
  state.presentationOrder = (state.dashboard?.presentation || [])
    .filter((team) => !team.deactivated)
    .map((team) => team.id);
  state.presentationEditing = true;
  render();
}

function resetPresentationState() {
  state.presentationEditing = false;
  state.presentationOrder = null;
  state.confirmingDeactivateTeamId = null;
}

function cancelPresentationEdit() {
  resetPresentationState();
  render();
}

function reorderPresentation(fromIndex, toIndex) {
  const order = state.presentationOrder;
  if (!order || toIndex < 0 || toIndex >= order.length || fromIndex === toIndex) return;
  const updated = [...order];
  const [moved] = updated.splice(fromIndex, 1);
  updated.splice(toIndex, 0, moved);
  state.presentationOrder = updated;
  render();
}

async function savePresentationOrder() {
  if (!state.presentationOrder || state.presentationSaving) return;
  state.presentationSaving = true;
  render();
  try {
    const response = await api(`/api/admin/presentation-order?pitch=${state.adminPitch}`, {
      method: 'PUT',
      body: JSON.stringify({ order: state.presentationOrder }),
    });
    resetPresentationState();
    setToast(response.message || 'Orden guardado.');
    await loadDashboard(state.activeTeamId, state.adminPitch);
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.presentationSaving = false;
    render();
  }
}

async function runPresentationTeamAction(teamId, action) {
  if (state.presentationBusyTeamId !== null) return;
  state.presentationBusyTeamId = teamId;
  state.confirmingDeactivateTeamId = null;
  render();
  try {
    const response = await api(`/api/admin/team/${teamId}/${action}?pitch=${state.adminPitch}`, {
      method: 'POST',
      body: '{}',
    });
    setToast(response.message || 'Cambio guardado.');
    await loadDashboard(state.activeTeamId, state.adminPitch);
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.presentationBusyTeamId = null;
    render();
  }
}

let presentationDragIndex = null;

function handlePresentationDragStart(event) {
  const row = event.target.closest('.presentation-row--editing');
  if (!row) return;
  presentationDragIndex = Number(row.dataset.presentationIndex);
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(presentationDragIndex));
  }
  row.classList.add('dragging');
}

function handlePresentationDragOver(event) {
  const row = event.target.closest('.presentation-row--editing');
  if (!row || presentationDragIndex === null) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
}

function handlePresentationDrop(event) {
  const row = event.target.closest('.presentation-row--editing');
  if (!row || presentationDragIndex === null) return;
  event.preventDefault();
  const fromIndex = presentationDragIndex;
  presentationDragIndex = null;
  reorderPresentation(fromIndex, Number(row.dataset.presentationIndex));
}

function handlePresentationDragEnd() {
  presentationDragIndex = null;
  document.querySelectorAll('.presentation-row.dragging').forEach((el) => el.classList.remove('dragging'));
}

function selectAdminPitch(pitch) {
  state.adminPitch = Number(pitch);
  if (state.adminPitch === 3) {
    render();
    return;
  }
  loadDashboard(null, state.adminPitch);
}

function backToPitchSelector() {
  if ((state.deliberating || state.presentationEditing) && !window.confirm('Tienes un orden sin guardar. Si sales, se perderán los cambios.')) {
    return;
  }
  state.deliberating = false;
  state.deliberationOrder = null;
  state.confirmingDeliberation = false;
  resetPresentationState();
  state.adminPitch = null;
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function activatePitchForJurors(pitch) {
  const pitchNum = Number(pitch);
  if (state.activatingPitch) return;
  state.activatingPitch = pitchNum;
  render();
  try {
    const response = await api('/api/admin/active-pitch', {
      method: 'PUT',
      body: JSON.stringify({ pitch: pitchNum }),
    });
    state.dashboard = { ...(state.dashboard || {}), active_pitch: response.active_pitch || pitchNum };
    setToast(response.message || `Pitch ${pitchNum} activado para los jurados.`);
  } catch (error) {
    setToast(error.message, 'error');
  } finally {
    state.activatingPitch = null;
    render();
  }
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

  if (!currentSummary) {
    const pitchLabel = dashboard.pitch_id ? `Pitch ${dashboard.pitch_id}` : 'este Pitch';
    const allDone = (progress.assigned || 0) > 0 && (progress.pending || 0) === 0;
    if (allDone) {
      return `
        <div class="jury-header stack">
          <section class="card jury-complete">
            <div class="jury-complete__icon" aria-hidden="true">✓</div>
            <p class="section-label">Evaluación completada</p>
            <h3>Ya no hay más equipos para calificar</h3>
            <p class="jury-complete__thanks">Muchas gracias por evaluar con criterio y profesionalismo.</p>
            <span class="badge">${progress.completed || progress.assigned} de ${progress.assigned} equipos calificados</span>
            <p class="muted jury-complete__note">Cuando el administrador active el siguiente Pitch, aparecerá aquí automáticamente.</p>
          </section>
        </div>
      `;
    }
    const emptyMessage = `Todavía no hay equipos por calificar en ${pitchLabel}. Espera a que el administrador lo active.`;
    return `
      <div class="jury-header stack">
        <section class="team-hero">
          <article class="team-card">
            <p class="section-label">Equipo actual</p>
            <h3>No hay equipos por calificar</h3>
            <p class="muted">${escapeHtml(emptyMessage)}</p>
          </article>
        </section>
      </div>
    `;
  }

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
        ${currentEvaluationCard}
      </section>

      <section class="navigation-card">
        <p class="section-label">Navegación</p>
        <h3>Flujo secuencial</h3>
        <p class="navigation-card__team">Equipo actual: <strong>${escapeHtml(currentSummary?.name || '—')}</strong></p>
        <div class="navigation-actions">
          <button class="button button--primary" type="button" data-action="jury-save" ${saveDisabled ? 'disabled' : ''}>Guardar evaluación</button>
        </div>
        <div class="inline-feedback">${state.juryDirty ? 'Tienes cambios sin guardar.' : 'Al guardar avanzas automáticamente al siguiente equipo pendiente.'}</div>
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

async function loadDashboard(teamId = null, pitchId = state.adminPitch || 1) {
  if (!state.authenticated || !state.user) return;
  const role = state.user.role;
  const activePitch = role === 'admin' ? Number(pitchId) || 1 : 1;
  const endpoint = role === 'admin'
    ? `/api/admin/dashboard?pitch=${activePitch}${teamId ? `&team_id=${teamId}` : ''}`
    : `/api/jury/dashboard${teamId ? `?team_id=${teamId}` : ''}`;

  state.loading = true;
  try {
    const payload = await api(endpoint);
    state.dashboard = payload.dashboard;
    if (role === 'admin') state.adminPitch = activePitch;
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
  state.adminPitch = null;
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

  if (action === 'select-admin-pitch') {
    selectAdminPitch(actionElement.dataset.pitch);
    return;
  }

  if (action === 'juror-deactivate') {
    state.confirmingDeactivateJurorId = Number(actionElement.dataset.jurorId);
    render();
    return;
  }

  if (action === 'juror-cancel-deactivate') {
    state.confirmingDeactivateJurorId = null;
    render();
    return;
  }

  if (action === 'juror-confirm-deactivate') {
    setJurorActive(state.confirmingDeactivateJurorId, false);
    return;
  }

  if (action === 'juror-activate') {
    setJurorActive(Number(actionElement.dataset.jurorId), true);
    return;
  }

  if (action === 'presentation-edit') {
    startPresentationEdit();
    return;
  }

  if (action === 'presentation-cancel') {
    cancelPresentationEdit();
    return;
  }

  if (action === 'presentation-save') {
    savePresentationOrder();
    return;
  }

  if (action === 'presentation-move-up' || action === 'presentation-move-down') {
    const index = Number(actionElement.dataset.presentationIndex);
    reorderPresentation(index, action === 'presentation-move-up' ? index - 1 : index + 1);
    return;
  }

  if (action === 'presentation-move-end') {
    runPresentationTeamAction(Number(actionElement.dataset.teamId), 'move-to-end');
    return;
  }

  if (action === 'presentation-deactivate') {
    state.confirmingDeactivateTeamId = Number(actionElement.dataset.teamId);
    render();
    return;
  }

  if (action === 'presentation-cancel-deactivate') {
    state.confirmingDeactivateTeamId = null;
    render();
    return;
  }

  if (action === 'presentation-confirm-deactivate') {
    runPresentationTeamAction(state.confirmingDeactivateTeamId, 'deactivate');
    return;
  }

  if (action === 'presentation-reactivate') {
    runPresentationTeamAction(Number(actionElement.dataset.teamId), 'reactivate');
    return;
  }

  if (action === 'back-to-pitch-selector') {
    backToPitchSelector();
    return;
  }

  if (action === 'activate-pitch') {
    activatePitchForJurors(actionElement.dataset.pitch);
    return;
  }

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
  if (state.role === 'admin' && !state.adminPitch) {
    await loadBootstrap();
    return;
  }
  if (state.role === 'admin' && state.adminPitch === 3) {
    render();
    return;
  }
  await loadDashboard(state.activeTeamId, state.adminPitch || 1);
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
  dom.dashboardRoot.addEventListener('dragstart', handlePresentationDragStart);
  dom.dashboardRoot.addEventListener('dragover', handlePresentationDragOver);
  dom.dashboardRoot.addEventListener('drop', handlePresentationDrop);
  dom.dashboardRoot.addEventListener('dragend', handlePresentationDragEnd);
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