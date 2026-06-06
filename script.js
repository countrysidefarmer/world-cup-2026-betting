'use strict';

let _data = null;
let _sortCol = 'ev_total';
let _sortAsc = false;
let _groupFilter = 'all';
let _openDetail = null;

// ── Bootstrap ──────────────────────────────────────────────────────────────

fetch('./data/teams.json?v=' + Date.now())
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(d => {
    _data = d;
    if (!d.teams || d.teams.length === 0) {
      showMsg('⏳', 'Data not yet available — the fetcher may be warming up. Check back soon.');
      return;
    }
    checkStale(d.last_updated);
    renderCards(d);
    renderTable();
    updateNavTimestamp(d.last_updated);
  })
  .catch(err => {
    console.error(err);
    showMsg('⚠️', 'Could not load team data. Please try again later.');
  });

// ── Helpers ────────────────────────────────────────────────────────────────

function fmt_ev(v)  { return (v != null) ? v.toFixed(1) : '—'; }
function fmt_pct(v) { return (v != null) ? (v * 100).toFixed(1) + '%' : '—'; }
function esc(s)     { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function quality_badge(q) {
  if (q === 'full') return '<span class="badge badge-full">Live</span>';
  return '<span class="badge badge-prior">Prior</span>';
}

function showMsg(icon, msg) {
  document.getElementById('table-body').innerHTML =
    `<tr><td colspan="12"><div class="state-msg"><div class="icon">${icon}</div>${msg}</div></td></tr>`;
}

function checkStale(ts) {
  if (!ts) return;
  const age_h = (Date.now() - new Date(ts)) / 3_600_000;
  if (age_h > 48) document.getElementById('stale-banner').style.display = 'block';
}

function updateNavTimestamp(ts) {
  if (!ts) return;
  const d = new Date(ts);
  document.getElementById('nav-updated').textContent =
    d.toLocaleDateString('en-GB', { day:'numeric', month:'short', hour:'2-digit', minute:'2-digit', timeZoneName:'short' });
}

// ── Stat cards ─────────────────────────────────────────────────────────────

function renderCards(data) {
  const teams = data.teams;
  if (!teams.length) return;

  // Top EV
  const top = teams[0];
  document.getElementById('card-top-ev-val').textContent = fmt_ev(top.ev_total);
  document.getElementById('card-top-ev-name').textContent = top.flag + ' ' + top.name;

  // Most likely winner
  const fav = [...teams].sort((a, b) => b.probs.p_win - a.probs.p_win)[0];
  document.getElementById('card-fav-name').textContent = fav.flag + ' ' + fav.name;
  document.getElementById('card-fav-pct').textContent = fmt_pct(fav.probs.p_win) + ' win probability';

  // Dark horse: highest ev_total / p_win ratio (among teams with live data)
  const live = teams.filter(t => t.data_quality === 'full' && t.probs.p_win > 0.001);
  if (live.length) {
    const dark = live.reduce((best, t) => {
      const ratio = t.ev_total / t.probs.p_win;
      return ratio > (best.ev_total / best.probs.p_win) ? t : best;
    });
    document.getElementById('card-dark-name').textContent = dark.flag + ' ' + dark.name;
    document.getElementById('card-dark-sub').textContent =
      fmt_ev(dark.ev_total) + ' EV · ' + fmt_pct(dark.probs.p_win) + ' win';
  }

  // Coverage
  const live_count = teams.filter(t => t.data_quality === 'full').length;
  document.getElementById('card-coverage').textContent = live_count + ' / 48';
}

// ── Sorting ────────────────────────────────────────────────────────────────

function sortTable(col) {
  if (_sortCol === col) {
    _sortAsc = !_sortAsc;
  } else {
    _sortCol = col;
    _sortAsc = false;  // default: descending (high = good)
  }
  renderTable();
  updateSortHeaders();
}

function updateSortHeaders() {
  document.querySelectorAll('thead th').forEach(th => {
    th.classList.remove('sorted', 'asc');
  });
  const colMap = {
    'ev_total':         3,
    'ev_group':         4,
    'ev_tournament':    5,
    'ev_entertainment': 6,
    'p_win':            7,
    'p_final':          8,
    'p_advance':        9,
  };
  const idx = colMap[_sortCol];
  if (idx != null) {
    const th = document.querySelectorAll('thead th')[idx];
    if (th) {
      th.classList.add('sorted');
      if (_sortAsc) th.classList.add('asc');
    }
  }
}

// ── Group filter ───────────────────────────────────────────────────────────

function filterGroup(g, btn) {
  _groupFilter = g;
  _openDetail  = null;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTable();
}

// ── Table render ───────────────────────────────────────────────────────────

function getValue(team, col) {
  if (col.startsWith('p_')) return team.probs[col] ?? 0;
  return team[col] ?? 0;
}

function renderTable() {
  if (!_data || !_data.teams) return;

  let teams = _data.teams;
  if (_groupFilter !== 'all') {
    teams = teams.filter(t => t.group === _groupFilter);
  }

  teams = [...teams].sort((a, b) => {
    const diff = getValue(b, _sortCol) - getValue(a, _sortCol);
    return _sortAsc ? -diff : diff;
  });

  const tbody = document.getElementById('table-body');
  if (teams.length === 0) {
    tbody.innerHTML = '<tr><td colspan="12"><div class="state-msg"><div class="icon">🔍</div>No teams in this group.</div></td></tr>';
    return;
  }

  const rows = [];
  teams.forEach((team, idx) => {
    const rank = idx + 1;
    const rankClass = rank <= 3 ? ' top3' : '';
    rows.push(`
<tr data-key="${esc(team.key)}">
  <td class="cell-rank${rankClass}">${rank}</td>
  <td><div class="cell-team"><span class="flag">${esc(team.flag)}</span><span class="team-name">${esc(team.name)}</span></div></td>
  <td class="cell-group"><span>Grp ${esc(team.group)}</span></td>
  <td class="cell-ev ev-total">${fmt_ev(team.ev_total)}</td>
  <td class="cell-ev col-ev-group">${fmt_ev(team.ev_group)}</td>
  <td class="cell-ev col-ev-tournament">${fmt_ev(team.ev_tournament)}</td>
  <td class="cell-ev col-ev-ent">${fmt_ev(team.ev_entertainment)}</td>
  <td class="cell-prob">${fmt_pct(team.probs.p_win)}</td>
  <td class="cell-prob">${fmt_pct(team.probs.p_final)}</td>
  <td class="cell-prob">${fmt_pct(team.probs.p_advance)}</td>
  <td class="cell-quality">${quality_badge(team.data_quality)}</td>
  <td class="cell-expand"><button id="expand-${esc(team.key)}" onclick="toggleDetail('${esc(team.key)}')" title="Show breakdown">›</button></td>
</tr>`);

    // Re-open detail if it was open before re-render
    if (_openDetail === team.key) {
      rows.push(buildDetailRow(team));
    }
  });

  tbody.innerHTML = rows.join('');
  updateSortHeaders();
}

// ── Detail row ─────────────────────────────────────────────────────────────

function buildDetailRow(team) {
  const b = team.breakdown;
  const p = team.probs;
  return `
<tr class="detail-row" id="detail-${esc(team.key)}">
  <td colspan="12">
    <div class="detail-content">
      <div class="detail-section">
        <h5>Group Stage Probabilities</h5>
        <div class="detail-row-item"><span>Win group (1st)</span><span>${fmt_pct(p.p_win_group)}</span></div>
        <div class="detail-row-item"><span>Finish 2nd</span><span>${fmt_pct(b.p_grp_2nd)}</span></div>
        <div class="detail-row-item"><span>Finish 3rd</span><span>${fmt_pct(b.p_grp_3rd)}</span></div>
        <div class="detail-row-item"><span>Finish 4th</span><span>${fmt_pct(b.p_grp_4th)}</span></div>
        <div class="detail-row-item"><span>Advance from groups</span><span>${fmt_pct(p.p_advance)}</span></div>
      </div>
      <div class="detail-section">
        <h5>Tournament Stage Probabilities</h5>
        <div class="detail-row-item"><span>Reach QF</span><span>${fmt_pct(p.p_qf)}</span></div>
        <div class="detail-row-item"><span>Reach SF</span><span>${fmt_pct(p.p_sf)}</span></div>
        <div class="detail-row-item"><span>Reach Final</span><span>${fmt_pct(p.p_final)}</span></div>
        <div class="detail-row-item"><span>Win WC</span><span>${fmt_pct(p.p_win)}</span></div>
      </div>
      <div class="detail-section">
        <h5>EV Breakdown</h5>
        <div class="detail-row-item"><span>Group ranking EV</span><span>${fmt_ev(team.ev_group)} pts</span></div>
        <div class="detail-row-item"><span>Tournament EV</span><span>${fmt_ev(team.ev_tournament)} pts</span></div>
        <div class="detail-row-item"><span>Entertainment bonus EV</span><span>${fmt_ev(team.ev_entertainment)} pts</span></div>
        <div class="detail-row-item" style="border-top:1px solid var(--border);margin-top:6px;padding-top:6px;font-weight:600;color:var(--gold);">
          <span>Total EV</span><span>${fmt_ev(team.ev_total)} pts</span>
        </div>
        <div class="detail-row-item" style="margin-top:6px;">
          <span>Data source</span>
          <span>${team.data_quality === 'full' ? '✅ Live Polymarket' : '⚠ Prior estimate'}</span>
        </div>
      </div>
    </div>
  </td>
</tr>`;
}

function toggleDetail(key) {
  const existing = document.getElementById('detail-' + key);
  const btn = document.getElementById('expand-' + key);

  if (existing) {
    existing.remove();
    if (btn) btn.classList.remove('open');
    _openDetail = null;
  } else {
    // Close any other open detail
    if (_openDetail) {
      const old = document.getElementById('detail-' + _openDetail);
      if (old) old.remove();
      const oldBtn = document.getElementById('expand-' + _openDetail);
      if (oldBtn) oldBtn.classList.remove('open');
    }
    _openDetail = key;
    if (btn) btn.classList.add('open');

    const team = _data.teams.find(t => t.key === key);
    if (!team) return;

    const parentRow = document.querySelector(`tr[data-key="${key}"]`);
    if (parentRow) {
      parentRow.insertAdjacentHTML('afterend', buildDetailRow(team));
    }
  }
}

// ── Scoring panel ──────────────────────────────────────────────────────────

function toggleScoring() {
  const body = document.getElementById('scoring-body');
  const btn  = document.getElementById('scoring-toggle');
  body.classList.toggle('visible');
  btn.classList.toggle('open');
}
