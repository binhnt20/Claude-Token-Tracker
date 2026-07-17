"""Generate a self-contained HTML dashboard from a token usage report."""
from __future__ import annotations

import json
from pathlib import Path

import sys as _sys

# PyInstaller bundles assets into _MEIPASS temp dir
if getattr(_sys, "frozen", False):
    ASSETS_DIR = Path(_sys._MEIPASS) / "assets"
else:
    ASSETS_DIR = Path(__file__).parent / "assets"


def _load_asset(name: str) -> str:
    path = ASSETS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def generate_html(report: dict) -> str:
    """Return a complete self-contained HTML dashboard string."""
    chartjs = _load_asset("chart.min.js")
    flatpickr_css = _load_asset("flatpickr.min.css")
    flatpickr_dark_css = _load_asset("flatpickr-dark.css")
    flatpickr_js = _load_asset("flatpickr.min.js")
    data_json = json.dumps(report, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code Token Usage</title>
<style>
:root {{
  --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
  --text: #f1f5f9; --text2: #94a3b8; --accent: #818cf8;
  --green: #34d399; --orange: #fb923c; --pink: #f472b6;
  --blue: #60a5fa; --cyan: #22d3ee; --red: #f87171;
  --border: #475569; --radius: 12px;
}}
@media (prefers-color-scheme: light) {{
  :root {{
    --bg: #f1f5f9; --surface: #ffffff; --surface2: #e2e8f0;
    --text: #1e293b; --text2: #64748b; --border: #cbd5e1;
  }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5; padding: 24px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
header {{ display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }}
h1 {{ font-size: 28px; font-weight: 700; }}
.subtitle {{ color: var(--text2); font-size: 13px; }}

/* --- Filter Bar --- */
.filter-bar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }}
.filter-bar button {{
  background: var(--surface); color: var(--text2); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 14px; font-size: 13px; cursor: pointer;
  transition: all .15s;
}}
.filter-bar button:hover {{ border-color: var(--accent); color: var(--text); }}
.filter-bar button.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
.date-range {{ display: none; align-items: center; gap: 8px; }}
.date-range.visible {{ display: flex; }}
.date-input {{
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 12px; font-size: 13px; font-family: inherit;
  width: 130px; cursor: pointer;
}}
.date-input:focus {{ border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px rgba(129,140,248,0.25); }}
.date-range .range-hint {{ color: var(--text2); font-size: 11px; }}
.date-range .range-error {{ color: var(--red); font-size: 11px; font-weight: 500; }}

/* --- Cards --- */
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 32px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px; text-align: center; transition: transform .1s; }}
.card:hover {{ transform: translateY(-2px); }}
.card .value {{ font-size: 28px; font-weight: 700; }}
.card .label {{ color: var(--text2); font-size: 12px; margin-top: 4px; }}
.card .cost {{ color: var(--text2); font-size: 11px; margin-top: 6px; line-height: 1.4; }}
.card .cost span {{ color: #fbbf24; font-weight: 600; }}
.card:nth-child(1) .value {{ color: var(--accent); }}
.card:nth-child(2) .value {{ color: var(--green); }}
.card:nth-child(3) .value {{ color: var(--orange); }}
.card:nth-child(4) .value {{ color: var(--pink); }}
.card:nth-child(5) .value {{ color: var(--cyan); }}
.card:nth-child(6) .value {{ color: var(--blue); }}

/* --- Charts --- */
.charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px; margin-bottom: 32px; }}
@media (max-width: 768px) {{ .charts {{ grid-template-columns: 1fr; }} }}
.chart-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }}
.chart-box h2 {{ font-size: 16px; margin-bottom: 16px; }}
.chart-wrap {{ position: relative; height: 300px; }}

/* --- Table --- */
.table-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px; margin-bottom: 32px; overflow-x: auto; }}
.table-box h2 {{ font-size: 16px; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ text-align: left; padding: 10px 12px; border-bottom: 2px solid var(--border); color: var(--text2);
  font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }}
th:hover {{ color: var(--accent); }}
th.sorted-asc::after {{ content: ' \\25B2'; font-size: 10px; }}
th.sorted-desc::after {{ content: ' \\25BC'; font-size: 10px; }}
td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
tr:hover td {{ background: var(--surface2); }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.note {{ color: var(--text2); font-size: 12px; margin: -20px 0 28px; line-height: 1.5; }}
.badge {{ background: var(--surface2); color: var(--text2); border-radius: 6px;
  padding: 2px 8px; font-size: 11px; }}

footer {{ text-align: center; color: var(--text2); font-size: 12px; padding: 16px 0; }}
/* Flatpickr overrides for dashboard theme */
.flatpickr-calendar {{ background: var(--surface) !important; border: 1px solid var(--border) !important; border-radius: var(--radius) !important; box-shadow: 0 8px 24px rgba(0,0,0,0.4) !important; }}
.flatpickr-months .flatpickr-month {{ color: var(--text) !important; }}
.flatpickr-current-month .cur-month, .flatpickr-current-month .numInputWrapper {{ color: var(--text) !important; }}
.flatpickr-day {{ color: var(--text) !important; }}
.flatpickr-day.selected {{ background: var(--accent) !important; border-color: var(--accent) !important; }}
.flatpickr-day:hover {{ background: var(--surface2) !important; border-color: var(--surface2) !important; }}
.flatpickr-day.today {{ border-color: var(--accent) !important; }}
.flatpickr-day.disabled {{ color: var(--text2) !important; opacity: 0.3; }}
span.flatpickr-weekday {{ color: var(--text2) !important; }}
.flatpickr-months .flatpickr-prev-month, .flatpickr-months .flatpickr-next-month {{ fill: var(--text2) !important; color: var(--text2) !important; }}
.flatpickr-months .flatpickr-prev-month:hover, .flatpickr-months .flatpickr-next-month:hover {{ fill: var(--text) !important; color: var(--text) !important; }}
</style>
<style>{flatpickr_css}</style>
<style>{flatpickr_dark_css}</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>Claude Code Token Usage</h1>
      <div class="subtitle" id="subtitle"></div>
    </div>
  </header>

  <!-- Filter Bar -->
  <div class="filter-bar" id="filterBar">
    <button data-range="1" id="btnToday">Today</button>
    <button data-range="2">Yesterday</button>
    <button data-range="7">Last 7 days</button>
    <button data-range="30" class="active">Last 30 days</button>
    <button data-range="0">All time</button>
    <button data-range="custom" id="btnCustom">Custom</button>
    <div class="date-range" id="dateRange">
      <input type="text" class="date-input" id="dateFrom" placeholder="Start date" readonly>
      <span style="color:var(--text2)">—</span>
      <input type="text" class="date-input" id="dateTo" placeholder="End date" readonly>
      <span class="range-hint" id="rangeHint"></span>
    </div>
  </div>

  <div class="cards" id="cards"></div>
  <div class="note" id="unpricedNote"></div>

  <div class="charts">
    <div class="chart-box">
      <h2 id="chartTitle">Daily Token Usage</h2>
      <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
    </div>
    <div class="chart-box">
      <h2>By Model</h2>
      <div class="chart-wrap"><canvas id="modelChart"></canvas></div>
    </div>
  </div>

  <div class="table-box">
    <h2>Projects</h2>
    <table id="projectTable">
      <thead><tr>
        <th data-key="name">Project</th>
        <th data-key="total" class="num">Total Tokens</th>
        <th data-key="input" class="num">Input</th>
        <th data-key="output" class="num">Output</th>
        <th data-key="cache_5m" class="num">Cache 5m</th>
        <th data-key="cache_1h" class="num">Cache 1h</th>
        <th data-key="cache_read" class="num">Cache Read</th>
        <th data-key="requests" class="num">Requests</th>
        <th data-key="cost" class="num">Cost</th>
      </tr></thead>
      <tbody id="projectBody"></tbody>
    </table>
  </div>

  <footer>Generated by Claude Code Token Tracker</footer>
</div>

<script>{flatpickr_js}</script>
<script>{chartjs}</script>
<script>
const RAW = {data_json};

// ===================== Helpers =====================
function fmt(n) {{
  if (n >= 1e9) return (n/1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}}
function fmtFull(n) {{ return n.toLocaleString(); }}

function toDate(s) {{ return new Date(s + 'T00:00:00'); }}
function isoDate(d) {{ return d.toISOString().slice(0, 10); }}
function addDays(d, n) {{ const r = new Date(d); r.setDate(r.getDate() + n); return r; }}

const allDates = Object.keys(RAW.by_date).sort();
const todayStr = allDates.length ? allDates[allDates.length - 1] : isoDate(new Date());

// CSS vars for chart colors
const cs = getComputedStyle(document.body);
const txtColor = () => cs.getPropertyValue('--text2').trim();
const borderColor = () => cs.getPropertyValue('--border').trim();

const modelColors = {{
  'claude-opus-4-8': '#818cf8',
  'claude-fable-5': '#a78bfa',
  'claude-sonnet-5': '#2dd4bf',
  'claude-sonnet-4-6': '#34d399',
  'claude-haiku-4-5': '#fb923c',
}};
function getColor(m, i) {{ return modelColors[m] || ['#f472b6','#22d3ee','#60a5fa','#f87171'][i % 4]; }}

// Costs are computed in pricing.py and arrive on the report. This file never prices anything.
const USD_TO_VND = 25_850;

function fmtUSD(n) {{
  if (n >= 1000) return '$' + (n/1000).toFixed(1) + 'K';
  if (n >= 1) return '$' + n.toFixed(2);
  if (n >= 0.01) return '$' + n.toFixed(2);
  return '$' + n.toFixed(4);
}}
function fmtVND(n) {{
  if (n >= 1e9) return (n/1e9).toFixed(1) + 'B VND';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M VND';
  if (n >= 1e3) return (n/1e3).toFixed(0) + 'K VND';
  return Math.round(n) + ' VND';
}}

// ===================== Date filter state =====================
let filterFrom = null; // null = no lower bound
let filterTo = null;   // null = no upper bound

function filteredDates() {{
  return allDates.filter(d => {{
    if (filterFrom && d < filterFrom) return false;
    if (filterTo && d > filterTo) return false;
    return true;
  }});
}}

// ===================== Compute filtered stats =====================
function billableTokens(v) {{
  return v.input_tokens + v.output_tokens + v.cache_creation_input_tokens;
}}

function computeStats(dates) {{
  const summary = {{ input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0,
                    cache_creation_5m_tokens: 0, cache_creation_1h_tokens: 0,
                    cache_read_input_tokens: 0, requests: 0 }};
  const byModel = {{}};
  let costUSD = 0;
  const costByModel = {{}};
  let unpricedRequests = 0, unpricedTokens = 0;
  const unpricedModels = new Set();

  dates.forEach(d => {{
    const dm = RAW.by_date[d];
    if (!dm) return;
    Object.entries(dm).forEach(([m, v]) => {{
      summary.input_tokens += v.input_tokens;
      summary.output_tokens += v.output_tokens;
      summary.cache_creation_input_tokens += v.cache_creation_input_tokens;
      summary.cache_creation_5m_tokens += v.cache_creation_5m_tokens;
      summary.cache_creation_1h_tokens += v.cache_creation_1h_tokens;
      summary.cache_read_input_tokens += v.cache_read_input_tokens;
      summary.requests += v.requests;
      byModel[m] = (byModel[m] || 0) + billableTokens(v);

      // v.cost is null when the model has no price table; token counts still apply.
      if (v.cost) {{
        costUSD += v.cost.total;
        costByModel[m] = (costByModel[m] || 0) + v.cost.total;
      }}
      if (v.unpriced_requests) {{
        unpricedRequests += v.unpriced_requests;
        unpricedTokens += v.unpriced_tokens;
        unpricedModels.add(m);
      }}
    }});
  }});
  summary.total_tokens = billableTokens(summary);

  return {{ summary, byModel, costUSD, costByModel,
           unpricedRequests, unpricedTokens, unpricedModels }};
}}

// ===================== Render functions =====================
let dailyChart = null;
let modelChart = null;

function renderCards(summary, costUSD) {{
  const costVND = costUSD * USD_TO_VND;
  const costHtml = `<div class="cost">API-equivalent <span>${{fmtUSD(costUSD)}}</span> / <span>${{fmtVND(costVND)}}</span></div>`;

  const cardsData = [
    ['Total Tokens', fmt(summary.total_tokens), fmtFull(summary.total_tokens), costHtml],
    ['Input Tokens', fmt(summary.input_tokens), fmtFull(summary.input_tokens), ''],
    ['Output Tokens', fmt(summary.output_tokens), fmtFull(summary.output_tokens), ''],
    ['Cache Created', fmt(summary.cache_creation_input_tokens), fmtFull(summary.cache_creation_input_tokens), ''],
    ['Cache Read', fmt(summary.cache_read_input_tokens), fmtFull(summary.cache_read_input_tokens), ''],
    ['Requests', fmt(summary.requests), fmtFull(summary.requests), ''],
  ];
  const el = document.getElementById('cards');
  el.innerHTML = '';
  cardsData.forEach(([label, display, title, extra]) => {{
    const d = document.createElement('div');
    d.className = 'card';
    d.innerHTML = `<div class="value" title="${{title}}">${{display}}</div><div class="label">${{label}}</div>${{extra}}`;
    el.appendChild(d);
  }});
}}

// The cost figure silently omits these requests otherwise.
function renderUnpricedNote(stats) {{
  const note = document.getElementById('unpricedNote');
  note.textContent = stats.unpricedRequests
    ? `Cost excludes ${{fmtFull(stats.unpricedRequests)}} requests across `
      + `${{stats.unpricedModels.size}} model(s) with no price table `
      + `(${{fmt(stats.unpricedTokens)}} tokens, counted above).`
    : '';
}}

function renderDailyChart(dates) {{
  const totals = {{}};
  dates.forEach(d => Object.entries(RAW.by_date[d] || {{}}).forEach(([m, v]) => {{
    totals[m] = (totals[m] || 0) + billableTokens(v);
  }}));
  // Drop models with nothing to plot (e.g. '<synthetic>', which never reports tokens).
  const models = Object.keys(totals).filter(m => totals[m] > 0);

  const maxBarPx = 80;
  const datasets = models.map((m, i) => ({{
    label: m.replace('claude-', ''),
    data: dates.map(d => {{
      const v = RAW.by_date[d]?.[m];
      return v ? billableTokens(v) : 0;
    }}),
    backgroundColor: getColor(m, i),
    maxBarThickness: maxBarPx,
  }}));

  const labels = dates.map(d => d.slice(5)); // MM-DD

  if (dailyChart) {{ dailyChart.destroy(); }}

  document.getElementById('chartTitle').textContent =
    'Daily Token Usage' + (dates.length < allDates.length ? ` (${{dates.length}} days)` : ' (all time)');

  dailyChart = new Chart(document.getElementById('dailyChart'), {{
    type: 'bar',
    data: {{ labels, datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ color: txtColor(), boxWidth: 12 }} }},
        tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': ' + fmt(ctx.raw) }} }}
      }},
      scales: {{
        x: {{ stacked: true, ticks: {{ color: txtColor(), maxRotation: 45 }}, grid: {{ display: false }} }},
        y: {{ stacked: true, ticks: {{ color: txtColor(), callback: v => fmt(v) }}, grid: {{ color: borderColor() }} }}
      }}
    }}
  }});
}}

function renderModelChart(byModel) {{
  const labels = Object.keys(byModel).filter(m => byModel[m] > 0);
  const values = labels.map(m => byModel[m]);

  if (modelChart) {{ modelChart.destroy(); }}

  modelChart = new Chart(document.getElementById('modelChart'), {{
    type: 'doughnut',
    data: {{
      labels: labels.map(m => m.replace('claude-', '')),
      datasets: [{{ data: values, backgroundColor: labels.map((m,i) => getColor(m,i)), borderWidth: 0 }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ color: txtColor(), boxWidth: 12 }} }},
        tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': ' + fmt(ctx.raw) }} }}
      }}
    }}
  }});
}}

// --- Project Table (uses RAW.by_project, all-time) ---
let projects = [];
let sortKey = 'total', sortAsc = false;

function buildProjectList(dates) {{
  // Rebuild project totals from by_date -> we need per-project-per-date data
  // RAW doesn't have that granularity, so show all-time project breakdown
  projects = Object.entries(RAW.by_project).map(([name, v]) => ({{
    name,
    total: billableTokens(v),
    input: v.input_tokens,
    output: v.output_tokens,
    cache_5m: v.cache_creation_5m_tokens,
    cache_1h: v.cache_creation_1h_tokens,
    cache_read: v.cache_read_input_tokens,
    requests: v.requests,
    cost: v.cost ? v.cost.total : null,
    unpricedRequests: v.unpriced_requests,
  }}));
}}

// A project can mix priced and unpriced models, so its cost may cover only part of
// its requests. Showing a bare dollar figure would imply it covers all of them.
function costCell(p) {{
  if (p.cost === null) return '<span class="badge">unpriced</span>';
  if (!p.unpricedRequests) return fmtUSD(p.cost);
  const covered = p.requests - p.unpricedRequests;
  return `${{fmtUSD(p.cost)}} <span class="badge" title="${{fmtFull(p.unpricedRequests)}} of `
    + `${{fmtFull(p.requests)}} requests have no price table and are excluded from this cost">`
    + `${{Math.round(covered / p.requests * 100)}}%</span>`;
}}

function renderTable() {{
  const sorted = [...projects].sort((a, b) => {{
    const av = a[sortKey], bv = b[sortKey];
    if (typeof av === 'string') return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    // Unpriced projects have no cost; keep them at the bottom in both directions
    // rather than letting (null - number) poison the comparator with NaN.
    if (av === null || bv === null) {{
      if (av === bv) return 0;
      return av === null ? 1 : -1;
    }}
    return sortAsc ? av - bv : bv - av;
  }});
  const tbody = document.getElementById('projectBody');
  tbody.innerHTML = sorted.map(p =>
    `<tr><td>${{p.name}}</td>` +
    `<td class="num" title="${{fmtFull(p.total)}}">${{fmt(p.total)}}</td>` +
    `<td class="num" title="${{fmtFull(p.input)}}">${{fmt(p.input)}}</td>` +
    `<td class="num" title="${{fmtFull(p.output)}}">${{fmt(p.output)}}</td>` +
    `<td class="num" title="${{fmtFull(p.cache_5m)}}">${{fmt(p.cache_5m)}}</td>` +
    `<td class="num" title="${{fmtFull(p.cache_1h)}}">${{fmt(p.cache_1h)}}</td>` +
    `<td class="num" title="${{fmtFull(p.cache_read)}}">${{fmt(p.cache_read)}}</td>` +
    `<td class="num" title="${{fmtFull(p.requests)}}">${{fmt(p.requests)}}</td>` +
    `<td class="num">${{costCell(p)}}</td></tr>`
  ).join('');
  document.querySelectorAll('#projectTable th').forEach(th => {{
    th.classList.remove('sorted-asc', 'sorted-desc');
    if (th.dataset.key === sortKey) th.classList.add(sortAsc ? 'sorted-asc' : 'sorted-desc');
  }});
}}

document.querySelectorAll('#projectTable th').forEach(th => {{
  th.addEventListener('click', () => {{
    const key = th.dataset.key;
    if (sortKey === key) sortAsc = !sortAsc;
    else {{ sortKey = key; sortAsc = false; }}
    renderTable();
  }});
}});

// ===================== Master render =====================
function renderAll() {{
  const dates = filteredDates();
  const stats = computeStats(dates);

  renderCards(stats.summary, stats.costUSD);
  renderUnpricedNote(stats);
  renderDailyChart(dates);
  renderModelChart(stats.byModel);
  buildProjectList(dates);
  renderTable();

  // Update subtitle
  let filterText = '';
  if (filterFrom && filterTo) filterText = filterFrom + ' to ' + filterTo;
  else if (filterFrom) filterText = 'from ' + filterFrom;
  else if (filterTo) filterText = 'to ' + filterTo;
  else filterText = 'All time';
  document.getElementById('subtitle').textContent =
    filterText + ' | ' + dates.length + ' days | Generated: ' + RAW.generated_at.slice(0, 19) + 'Z';
}}

// ===================== Filter bar logic =====================
function setRange(days) {{
  if (days === 0) {{
    // All time (max 180 days)
    if (allDates.length > 180) {{
      filterFrom = allDates[allDates.length - 180];
    }} else {{
      filterFrom = null;
    }}
    filterTo = null;
  }} else if (days === 1) {{
    // Today
    filterFrom = todayStr;
    filterTo = todayStr;
  }} else if (days === 2) {{
    // Yesterday
    const y = isoDate(addDays(toDate(todayStr), -1));
    filterFrom = y;
    filterTo = y;
  }} else {{
    // Last N days
    const capDays = Math.min(days, 180);
    filterFrom = isoDate(addDays(toDate(todayStr), -(capDays - 1)));
    filterTo = null;
  }}
  renderAll();
}}

// Quick buttons
document.querySelectorAll('.filter-bar button[data-range]').forEach(btn => {{
  btn.addEventListener('click', () => {{
    const range = btn.dataset.range;
    if (range === 'custom') return; // handled separately

    // Update active state
    document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Hide custom date range panel
    dateRangeEl.classList.remove('visible');

    setRange(parseInt(range));
  }});
}});

// ===================== Custom date range =====================
const btnCustom = document.getElementById('btnCustom');
const dateRangeEl = document.getElementById('dateRange');
const dateFromEl = document.getElementById('dateFrom');
const dateToEl = document.getElementById('dateTo');
const rangeHint = document.getElementById('rangeHint');

const fpOpts = {{
  dateFormat: 'Y-m-d',
  minDate: allDates.length ? allDates[0] : undefined,
  maxDate: todayStr,
  disableMobile: true,
  theme: 'dark',
}};

const fpFrom = flatpickr(dateFromEl, {{ ...fpOpts, onChange: () => applyCustomRange() }});
const fpTo = flatpickr(dateToEl, {{ ...fpOpts, onChange: () => applyCustomRange() }});

btnCustom.addEventListener('click', () => {{
  const isVisible = dateRangeEl.classList.contains('visible');
  if (isVisible) {{
    dateRangeEl.classList.remove('visible');
  }} else {{
    dateRangeEl.classList.add('visible');
    fpFrom.setDate(filterFrom || (allDates.length ? allDates[0] : todayStr), false);
    fpTo.setDate(filterTo || todayStr, false);
  }}
  document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
  btnCustom.classList.add('active');
}});

function applyCustomRange() {{
  const from = dateFromEl.value;
  const to = dateToEl.value;
  if (!from || !to) return;

  if (from > to) {{
    rangeHint.textContent = 'Start must be before end';
    rangeHint.className = 'range-error';
    return;
  }}

  rangeHint.textContent = '';
  rangeHint.className = 'range-hint';

  filterFrom = from;
  filterTo = to;
  renderAll();
}}

// ===================== Initial render (Last 30 days) =====================
setRange(30);
</script>
</body>
</html>"""
