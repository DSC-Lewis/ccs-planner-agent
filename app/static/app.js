/* CCS Planner · Briefing Agent — vanilla JS client.
 * Talks to the FastAPI backend under /api/*. Persists the session id in
 * localStorage so a refresh resumes the conversation.
 *
 * v4: multi-tenant — user logs in with an API key, the key is kept in
 * localStorage under `ccs.apiKey`, and every fetch() is routed through
 * apiFetch() so the X-API-Key header is attached automatically.
 */

/* ---------- Auth helpers (v4) ---------- */
const API_KEY_STORAGE = "ccs.apiKey";
function getApiKey() { return localStorage.getItem(API_KEY_STORAGE) || ""; }
function setApiKey(k) {
  if (k) localStorage.setItem(API_KEY_STORAGE, k);
  else localStorage.removeItem(API_KEY_STORAGE);
}
async function apiFetch(url, opts = {}) {
  const key = getApiKey();
  const headers = { ...(opts.headers || {}) };
  if (key) headers["X-API-Key"] = key;
  if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  return fetch(url, { ...opts, headers });
}

const STEP_LABELS = {
  survey_client:   "1. Survey & Client",
  project_dates:   "2. Project & Dates",
  target_audience: "3. Target Audience",
  planning_type:   "4. Planning Type",
  comms_setup:     "5. Comms Setup",
  channels:        "6. Channel Selection",
  calibration:     "7. Calibration & Costs",
  manual_plan:     "8. Manual Weekly Plan",
  criterion:       "7. Optimization Criterion",
  budget_channels: "8. Budget & Constraints",
  min_max:         "9. Min / Max Settings",
  optimize:        "10. Run Optimization",
  review:          "✓ Review & Save",
};

const MANUAL_ORDER = [
  "survey_client","project_dates","target_audience","planning_type","comms_setup",
  "channels","calibration","manual_plan","review"
];
const AUTO_ORDER = [
  "survey_client","project_dates","target_audience","planning_type","comms_setup",
  "channels","criterion","budget_channels","min_max","optimize","review"
];

const state = {
  mode: "manual",
  sessionId: null,
  session: null,
  prompt: "",
  opts: {},
  warnings: [],
  plan: null,
  introShown: {},  // { [sessionId]: true } — fires once per session switch
};

/* Mode-intro copy. Fires once when the user lands on the first
 * mode-specific step so they understand what each Agent actually does.
 * Addresses ivy-deployment feedback: "Manual 跟 Automatic 感覺一樣". */
const MANUAL_MODE_INTRO =
  "🧑‍💼 <b>Manual Agent</b> — 你主導。接下來我會把每個 channel × 每週的格子攤給你，"
  + "逐格填預算（TWD），我即時算 Reach / Frequency / GRP 給你看，完成就產出 <b>Plan 1</b>。";
const AUTO_MODE_INTRO =
  "🤖 <b>Automatic Agent</b> — 我主導。接下來你只要告訴我：<b>總預算</b>、"
  + "哪些 channel 必上（Mandatory）、哪些可選（Optional），以及最佳化目標（Net Reach / Attentive Reach…），"
  + "我會幫你解最佳化，直接產出 <b>Plan 2</b>。";

/* ---------- DOM helpers ---------- */
const $ = (sel) => document.querySelector(sel);
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of kids) {
    if (c == null) continue;
    n.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return n;
}
const scroll = $("#scroll");
function bubble(who, body) {
  const wrap = el("div", { class: `bubble ${who}` });
  const av = el("div", { class: `avatar ${who}` }, who === "bot" ? "CP" : "你");
  const msg = el("div", { class: "msg" });
  if (typeof body === "string") msg.innerHTML = body;
  else msg.append(body);
  wrap.append(av, msg);
  scroll.append(wrap);
  scroll.scrollTop = scroll.scrollHeight;
  return msg;
}
function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}
function botSay(html) { return bubble("bot", html); }
/**
 * Echo a user message back into the chat. Uses textContent via the bubble()
 * helper so any <script> or onerror attributes typed into an input field are
 * rendered as literal characters, not HTML.
 */
function userSay(text) { bubble("user", document.createTextNode(String(text))); }
function quickRow(options, onPick) {
  const row = el("div", { class: "quick" });
  options.forEach((o) => {
    const b = el("button", {
      class: o.primary ? "primary" : "",
      onclick: () => onPick(o.value ?? o.label, o),
    }, o.label);
    row.append(b);
  });
  return row;
}
function field(label, inputEl) {
  return el("div", { class: "field" }, el("label", {}, label), el("div", {}, inputEl));
}

/* ---------- Palette & Chart.js loader (FR-14 / NFR-4) ---------- */
// 6-colour palette — channel→colour mapping stays deterministic across charts.
const PALETTE = ["#2563EB", "#8B5CF6", "#EC4899", "#10B981", "#F59E0B", "#06B6D4"];
function colourFor(key, idx) {
  // deterministic hash-ish: hash string to index when idx not supplied
  if (typeof idx === "number") return PALETTE[idx % PALETTE.length];
  let h = 0;
  for (let i = 0; i < String(key).length; i++) h = (h * 31 + String(key).charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

let _chartLib = null;
async function loadChartLib() {
  if (_chartLib) return _chartLib;
  try {
    // Chart.js 4 via jsDelivr ESM build — no bundler needed.
    const mod = await import("https://cdn.jsdelivr.net/npm/chart.js@4.4.1/+esm");
    _chartLib = mod.Chart;
    _chartLib.register(...mod.registerables);
    return _chartLib;
  } catch (err) {
    console.warn("Chart.js CDN load failed — falling back to tables only", err);
    return null;
  }
}

/* ---------- API ---------- */
const api = {
  async create(mode) {
    const r = await apiFetch("/api/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async get(id) {
    const r = await apiFetch(`/api/sessions/${id}`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async advance(id, payload) {
    const r = await apiFetch(`/api/sessions/${id}/advance`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async health() {
    const r = await apiFetch("/api/health");
    return r.ok ? r.json() : null;
  },
  async comparePlans(ids) {
    const r = await apiFetch("/api/plans/compare", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ids)
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async fork(id, target_mode) {
    const r = await apiFetch(`/api/sessions/${id}/fork`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_mode })
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async listPlans(brief_id) {
    const url = brief_id ? `/api/plans?brief_id=${encodeURIComponent(brief_id)}` : "/api/plans";
    const r = await apiFetch(url);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async me() {
    const r = await apiFetch("/api/me");
    return r.ok ? r.json() : null;
  },
  async listProjects() {
    const r = await apiFetch("/api/projects");
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async createProject(name) {
    const r = await apiFetch("/api/projects", { method: "POST", body: JSON.stringify({ name }) });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async archiveProject(id) {
    const r = await apiFetch(`/api/projects/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async projectSessions(id) {
    const r = await apiFetch(`/api/projects/${id}/sessions`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async projectPlans(id) {
    const r = await apiFetch(`/api/projects/${id}/plans`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async conversation(sid) {
    const r = await apiFetch(`/api/sessions/${sid}/conversation`);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
};

/* ---------- Rendering ---------- */
function renderSidebar() {
  const order = state.mode === "manual" ? MANUAL_ORDER : AUTO_ORDER;
  const current = state.session?.step;
  const box = $("#steps");
  box.innerHTML = "";
  const currentIdx = order.indexOf(current);
  order.forEach((k, i) => {
    const cls = i < currentIdx ? "done" : (i === currentIdx ? "active" : "");
    const row = el("div", { class: `step ${cls}` },
      el("div", { class: "bullet" }, i < currentIdx ? "✓" : String(i + 1)),
      el("div", {}, STEP_LABELS[k] || k)
    );
    box.append(row);
  });
  // (Mode copy used to go here — now shown as the active topbar button
  //  + the Summary pill; don't duplicate it in the sidebar.)
  const pill = $("#modePill");
  pill.textContent = state.mode === "manual" ? "Manual" : "Automatic";
  pill.className = "pill " + (state.mode === "manual" ? "mode-manual" : "mode-auto");
}

function renderSummary() {
  const b = state.session?.brief;
  const box = $("#sumBody"); box.innerHTML = "";
  if (!b) { box.append(el("div", { class: "empty" }, "尚未建立 session")); return; }
  const add = (k, v) => box.append(el("div", { class: "sum-row" }, el("span", {}, k), el("b", {}, v ?? "—")));
  add("Survey", b.survey_id);
  add("Client", b.client_id);
  add("Project", b.project_name);
  add("Period", `${b.start_date} → ${b.end_date} (${b.weeks}w)`);
  add("Planning type", b.planning_type);

  const ta = el("div", { class: "sum-section" }, el("h6", {}, "Target Audiences"));
  if (b.target_ids?.length) {
    b.target_ids.forEach((t) => ta.append(el("div", { class: "ch-item" }, el("div", {}, t), el("div", {}))));
  } else ta.append(el("div", { class: "empty" }, "尚未選擇"));
  box.append(ta);

  if (b.planning_type === "Comm" && b.comms) {
    const s = el("div", { class: "sum-section" }, el("h6", {}, "Comms Setup"));
    const c = b.comms;
    [["Brand Strength", c.brand_strength], ["Parent Brand", c.parent_brand],
     ["Competitor Clutter", c.competitor_clutter], ["New Creative", c.new_creative],
     ["Message Complexity", c.message_complexity]].forEach(([k, v]) => {
      s.append(el("div", { class: "sum-row" }, el("span", {}, k), el("b", {}, `${v}/10`)));
    });
    s.append(el("div", { class: "sum-row" }, el("span", {}, "KPIs"),
      el("b", {}, c.kpi_ids?.length ? c.kpi_ids.join(", ") : "—")));
    box.append(s);
  }

  const ch = el("div", { class: "sum-section" }, el("h6", {}, `Channels (${b.channel_ids?.length || 0})`));
  if (b.channel_ids?.length) b.channel_ids.forEach((c) => ch.append(el("div", { class: "ch-item" }, el("div", {}, c))));
  else ch.append(el("div", { class: "empty" }, "尚未選擇 channel"));
  box.append(ch);

  if (state.plan) {
    const s = el("div", { class: "sum-section" }, el("h6", {}, `${state.plan.kind} · ${state.plan.name}`));
    const sum = state.plan.summary;
    [["Total Budget", sum.total_budget_twd?.toLocaleString()],
     ["Total Impressions", sum.total_impressions?.toLocaleString()],
     ["Total GRP", sum.total_grp],
     ["Net Reach %", sum.net_reach_pct],
     ["Frequency", sum.frequency]].forEach(([k, v]) => {
      s.append(el("div", { class: "sum-row" }, el("span", {}, k), el("b", {}, v ?? "—")));
    });
    box.append(s);
  }
}

function showWarnings(msg) {
  if (!state.warnings?.length) return;
  const w = el("div", { class: "warn-box" }, "⚠️ " + state.warnings.join(" · "));
  msg.append(w);
}

/* ---------- Step renderers ---------- */
async function renderStep() {
  const s = state.session?.step;
  const msg = botSay(state.prompt || "…");
  showWarnings(msg);
  switch (s) {
    case "survey_client":   renderSurveyClient(msg); break;
    case "project_dates":   renderProjectDates(msg); break;
    case "target_audience": renderTargetAudience(msg); break;
    case "planning_type":   renderPlanningType(msg); break;
    case "comms_setup":     renderCommsSetup(msg); break;
    case "channels":        renderChannels(msg); break;
    case "calibration":     renderCalibration(msg); break;
    case "manual_plan":     renderManualPlan(msg); break;
    case "criterion":       renderCriterion(msg); break;
    case "budget_channels": renderBudgetChannels(msg); break;
    case "min_max":         renderMinMax(msg); break;
    case "optimize":        renderOptimize(msg); break;
    case "review":          renderReview(msg); break;
    default: msg.append(el("div", {}, `Unknown step: ${s}`));
  }
}

function renderSurveyClient(msg) {
  const b = state.session.brief;
  const { surveys = [], clients = [] } = state.opts;
  const card = el("div", { class: "card" });
  const sv = el("select", { id: "survey-sel" }, el("option", { value: "" }, "請選擇"),
    ...surveys.map(s => el("option", { value: s.id, ...(b.survey_id === s.id ? { selected: "" } : {}) },
      `${s.label}${s.deprecated ? " (deprecated)" : ""}`)));
  const cl = el("select", { id: "client-sel" }, el("option", { value: "" }, "請選擇"),
    ...clients.map(c => el("option", { value: c.id, ...(b.client_id === c.id ? { selected: "" } : {}) }, c.label)));
  card.append(el("h5", {}, "Step 1 · Survey & Client"), field("Survey", sv), field("Client", cl));
  msg.append(card);
  msg.append(quickRow([
    { label: "套用影片示範 (Taiwan 2025 / Internal Pitch)" },
    { label: "確認 ▶", primary: true, value: "confirm" }
  ], async (v, o) => {
    if (o.label.startsWith("套用")) {
      sv.value = "tw_2025"; cl.value = "internal_pitch";
      return;
    }
    try {
      await submit({ survey_id: sv.value, client_id: cl.value });
      userSay(`Survey：${sv.value} ｜ Client：${cl.value}`);
    } catch (e) { showError(e); }
  }));
}

function renderProjectDates(msg) {
  const b = state.session.brief;
  const card = el("div", { class: "card" });
  const pn = el("input", { id: "pn", placeholder: "例如：test 260128", value: b.project_name || "" });
  const sd = el("input", { id: "sd", type: "date", value: b.start_date });
  const wk = el("select", { id: "wk" },
    ...(state.opts.week_options || [2,3,4,5,6,8,12]).map(w =>
      el("option", { value: w, ...(w === b.weeks ? { selected: "" } : {}) }, w + " weeks")));
  const ed = el("input", { id: "ed", type: "date", value: b.end_date, readonly: "" });
  function updateEnd() {
    const d = new Date(sd.value);
    if (!isNaN(d)) {
      d.setDate(d.getDate() + parseInt(wk.value, 10) * 7 - 1);
      ed.value = d.toISOString().slice(0, 10);
    }
  }
  sd.addEventListener("input", updateEnd);
  wk.addEventListener("change", updateEnd);
  card.append(
    el("h5", {}, "Step 2 · Project & Dates"),
    field("Project Name", pn), field("Start Date", sd),
    field("Weeks", wk), field("End Date", ed)
  );
  msg.append(card);
  msg.append(quickRow([
    { label: "套用影片示範 (test 260128 / 2026-02-16 / 4w)" },
    { label: "確認 ▶", primary: true }
  ], async (v, o) => {
    if (o.label.startsWith("套用")) {
      pn.value = "test 260128"; sd.value = "2026-02-16"; wk.value = "4"; updateEnd(); return;
    }
    try {
      await submit({
        project_name: pn.value,
        start_date: sd.value,
        weeks: parseInt(wk.value, 10)
      });
      userSay(`${pn.value} ｜ ${sd.value} → ${ed.value} (${wk.value}w)`);
    } catch (e) { showError(e); }
  }));
}

function renderTargetAudience(msg) {
  const b = state.session.brief;
  const targets = state.opts.targets || [];
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, "Step 3 · Target Audience（可多選）"));
  const tbl = el("table", { class: "tbl" });
  tbl.append(el("thead", {}, el("tr", {},
    el("th", {}), el("th", {}, "Target Name"), el("th", {}, "Source"),
    el("th", { class: "num" }, "Sample"), el("th", { class: "num" }, "Universe (000)"),
    el("th", { class: "num" }, "% of Base"))));
  const tbody = el("tbody", {});
  targets.forEach(t => {
    const cb = el("input", { type: "checkbox", value: t.id, ...(b.target_ids.includes(t.id) ? { checked: "" } : {}) });
    tbody.append(el("tr", {},
      el("td", {}, cb),
      el("td", {}, t.name),
      el("td", {}, el("span", { class: "tag" }, t.source)),
      el("td", { class: "num" }, (t.sample || 0).toLocaleString()),
      el("td", { class: "num" }, (t.universe_000 || 0).toLocaleString()),
      el("td", { class: "num" }, (t.base_pct || 0).toFixed(2) + "%")));
  });
  tbl.append(tbody);
  card.append(tbl);
  msg.append(card);
  msg.append(quickRow([
    { label: "套用影片示範 (All adults + TA 30-54 A)" },
    { label: "確認 ▶", primary: true }
  ], async (v, o) => {
    if (o.label.startsWith("套用")) {
      [...tbl.querySelectorAll("input")].forEach(x => x.checked = (x.value === "all_adults" || x.value === "ta_30_54_a"));
      return;
    }
    const picks = [...tbl.querySelectorAll("input:checked")].map(x => x.value);
    try {
      await submit({ target_ids: picks });
      userSay("Targets：" + picks.join(" + "));
    } catch (e) { showError(e); }
  }));
}

function renderPlanningType(msg) {
  msg.append(quickRow([
    { label: "Reach", value: "Reach" },
    { label: "Comm", value: "Comm", primary: true }
  ], async (v) => {
    try {
      await submit({ planning_type: v });
      userSay("Planning type：" + v);
    } catch (e) { showError(e); }
  }));
}

function renderCommsSetup(msg) {
  const c = state.session.brief.comms || {
    brand_strength: 6, parent_brand: 5, competitor_clutter: 5,
    new_creative: 5, message_complexity: 5, kpi_ids: []
  };
  const kpis = state.opts.brand_kpis || [];
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, "Step 5 · Brand & Message 校準 (0–10)"));
  const values = { ...c };
  function mkSlider(key, label) {
    const out = el("output", {}, String(values[key] ?? 5));
    const inp = el("input", { type: "range", min: 0, max: 10, step: 1, value: values[key] ?? 5 });
    inp.addEventListener("input", () => { values[key] = parseInt(inp.value, 10); out.textContent = inp.value; });
    return el("div", { class: "slider-row" }, el("label", {}, label), inp, out);
  }
  card.append(
    mkSlider("brand_strength", "Brand Strength"),
    mkSlider("parent_brand", "Parent Brand Strength"),
    mkSlider("competitor_clutter", "Competitor Clutter"),
    mkSlider("new_creative", "New Creative"),
    mkSlider("message_complexity", "Message Complexity")
  );
  card.append(el("h5", { style: "margin-top:12px" }, "Brand KPI (建議至少 3 項)"));
  const kpiBox = el("div", {});
  kpis.forEach(k => {
    const id = "kpi-" + k.id;
    const wrap = el("label", { style: "display:inline-flex;align-items:center;gap:6px;margin:4px 12px 4px 0;font-size:13px" },
      el("input", { type: "checkbox", id, value: k.id, ...(c.kpi_ids?.includes(k.id) ? { checked: "" } : {}) }),
      k.label);
    kpiBox.append(wrap);
  });
  card.append(kpiBox);
  msg.append(card);
  msg.append(quickRow([{ label: "確認 ▶", primary: true }], async () => {
    const kpi_ids = [...kpiBox.querySelectorAll("input:checked")].map(x => x.value);
    try {
      await submit({ comms: { ...values, kpi_ids } });
      userSay(`Comms 校準完成 · KPI：${kpi_ids.join(", ")}`);
    } catch (e) { showError(e); }
  }));
}

function renderChannels(msg) {
  const b = state.session.brief;
  const groups = state.opts.channel_groups || [];
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, "Step 6 · Channel Selection"));
  groups.forEach(g => {
    const wrap = el("div", { style: "margin:8px 0" },
      el("div", { style: "font-weight:600;font-size:12px;color:#374151;margin-bottom:4px" }, g.label));
    g.children.forEach(c => {
      const id = "ch-" + c.id;
      wrap.append(el("label", { style: "display:inline-flex;align-items:center;gap:6px;margin:2px 12px 2px 0;font-size:13px" },
        el("input", { type: "checkbox", id, value: c.id, ...(b.channel_ids.includes(c.id) ? { checked: "" } : {}) }),
        c.label));
    });
    card.append(wrap);
  });
  msg.append(card);
  msg.append(quickRow([
    { label: "套用影片示範 (TV / YouTube / Meta Video)" },
    { label: "確認 ▶", primary: true }
  ], async (v, o) => {
    if (o.label.startsWith("套用")) {
      const want = ["tv_advertising", "youtube_video_ads", "meta_video_ads"];
      card.querySelectorAll("input[type=checkbox]").forEach(x => x.checked = want.includes(x.value));
      return;
    }
    const picks = [...card.querySelectorAll("input:checked")].map(x => x.value);
    try {
      await submit({ channel_ids: picks });
      userSay("Channels：" + picks.join(" / "));
    } catch (e) { showError(e); }
  }));
}

function renderCalibration(msg) {
  // Show the Manual-mode intro on first entry to the manual-specific path.
  const key = `manual:${state.sessionId}`;
  if (!state.introShown[key]) {
    state.introShown[key] = true;
    botSay(MANUAL_MODE_INTRO);
  }
  const metrics = state.opts.metrics || {};
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, "Step 7 · Channel Metrics preview"));
  const tbl = el("table", { class: "tbl" });
  tbl.append(el("thead", {}, el("tr", {},
    el("th", {}, "Channel"), el("th", {}, "Category"),
    el("th", { class: "num" }, "CPM (TWD)"),
    el("th", { class: "num" }, "Penetration"),
    el("th", { class: "num" }, "Attention"),
    el("th", { class: "num" }, "Engagement"))));
  const tb = el("tbody", {});
  Object.entries(metrics).forEach(([ch, m]) => {
    tb.append(el("tr", {}, el("td", {}, ch),
      el("td", {}, el("span", { class: "tag" }, m.category)),
      el("td", { class: "num" }, m.cpm_twd.toFixed(2)),
      el("td", { class: "num" }, m.penetration_pct.toFixed(2) + "%"),
      el("td", { class: "num" }, m.attention_pct.toFixed(2) + "%"),
      el("td", { class: "num" }, m.engagement_pct.toFixed(2) + "%")));
  });
  tbl.append(tb);
  card.append(tbl);
  msg.append(card);
  msg.append(quickRow([{ label: "繼續 ▶", primary: true }], async () => {
    try { await submit({}); userSay("確認 metrics，進入 weekly plan"); } catch (e) { showError(e); }
  }));
}

function renderManualPlan(msg) {
  const b = state.session.brief;
  const suggestions = state.opts.suggestions || {};
  const weeks = state.opts.weeks || b.weeks;
  const start = new Date(state.opts.start_date || b.start_date);
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, `Step 8 · Manual weekly plan (${weeks} weeks)`));
  const tbl = el("table", { class: "tbl" });
  const head = el("tr", {}, el("th", {}, "Channel"));
  for (let i = 0; i < weeks; i++) {
    const d = new Date(start); d.setDate(d.getDate() + i * 7);
    head.append(el("th", { class: "num" }, `W${i + 1} (${d.getMonth() + 1}/${d.getDate()})`));
  }
  head.append(el("th", { class: "num" }, "Total"));
  tbl.append(el("thead", {}, head));
  const tbody = el("tbody", {});
  const inputs = {};        // ch → [<input>]
  const channelTotals = {}; // ch → <td>
  b.channel_ids.forEach(ch => {
    inputs[ch] = [];
    const tr = el("tr", {}, el("td", {}, ch));
    for (let i = 0; i < weeks; i++) {
      const def = suggestions[ch]?.[i] ?? 0;
      const inp = el("input", { type: "number", min: 0, step: 100, value: def, style: "width:92px" });
      inp.addEventListener("input", recompute);
      inputs[ch].push(inp);
      tr.append(el("td", { class: "num" }, inp));
    }
    const totCell = el("td", { class: "num" }, "0");
    channelTotals[ch] = totCell;
    tr.append(totCell);
    tbody.append(tr);
  });
  tbl.append(tbody);
  const weekCells = Array(weeks).fill(0).map(() => el("th", { class: "num" }, "0"));
  const grandCell = el("th", { class: "num" }, "0");
  tbl.append(el("tfoot", {}, el("tr", {}, el("th", {}, "Total"), ...weekCells, grandCell)));
  card.append(tbl);

  // recompute() writes through *direct* element references — no
  // document.getElementById. Previously this relied on ids that were
  // only resolvable after msg.append(card), which ordered the first
  // recompute() call before the card was in the DOM and silently broke
  // the whole render (tests/test_manual_plan_render_safety.py).
  function recompute() {
    let grand = 0;
    const weekly = new Array(weeks).fill(0);
    Object.entries(inputs).forEach(([ch, cols]) => {
      let t = 0;
      cols.forEach((inp, i) => { const v = +inp.value || 0; t += v; weekly[i] += v; });
      grand += t;
      channelTotals[ch].textContent = t.toLocaleString();
    });
    weekly.forEach((v, i) => { weekCells[i].textContent = v.toLocaleString(); });
    grandCell.textContent = grand.toLocaleString();
  }
  recompute();
  msg.append(card);
  msg.append(quickRow([
    { label: "重設為系統建議" },
    { label: "確認並 Save plan ▶", primary: true }
  ], async (v, o) => {
    if (o.label === "重設為系統建議") {
      b.channel_ids.forEach(ch => inputs[ch].forEach((inp, i) => inp.value = suggestions[ch]?.[i] ?? 0));
      recompute(); return;
    }
    const weekly_budgets = {};
    b.channel_ids.forEach(ch => { weekly_budgets[ch] = inputs[ch].map(inp => +inp.value || 0); });
    try { await submit({ weekly_budgets }); userSay("已完成 weekly plan 並送出 Save"); } catch (e) { showError(e); }
  }));
}

function renderCriterion(msg) {
  // Show the Automatic-mode intro on first entry to the automatic-specific path.
  const key = `auto:${state.sessionId}`;
  if (!state.introShown[key]) {
    state.introShown[key] = true;
    botSay(AUTO_MODE_INTRO);
  }
  const a = state.session.automatic_input;
  const { criteria = [], strategies = [] } = state.opts;
  const card = el("div", { class: "card" });
  const cri = el("select", { id: "cri" },
    ...criteria.map(c => el("option", { value: c.id, ...(a.criterion_id === c.id ? { selected: "" } : {}) }, c.label)));
  const str = el("select", { id: "str" },
    ...strategies.map(s => el("option", { value: s.id, ...(a.strategy_id === s.id ? { selected: "" } : {}) }, s.label)));
  card.append(
    el("h5", {}, "Step 7 · Automatic Optimization 參數"),
    field("Optimization Criterion", cri),
    field("Optimization Strategy", str)
  );
  msg.append(card);
  msg.append(quickRow([{ label: "確認 ▶", primary: true }], async () => {
    try {
      await submit({ criterion_id: cri.value, strategy_id: str.value });
      userSay(`${cri.value} · ${str.value}`);
    } catch (e) { showError(e); }
  }));
}

function renderBudgetChannels(msg) {
  const b = state.session.brief;
  const a = state.session.automatic_input;
  const labels = state.opts.labels || {};
  const card = el("div", { class: "card" });
  const bud = el("input", { id: "bud", type: "number", min: 0, step: 1000, value: a.total_budget_twd || "" });
  card.append(el("h5", {}, "Step 8 · Budget & Mandatory/Optional"),
    field("Total Budget (TWD)", bud));
  const mandBox = el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;margin:6px 0" });
  const optBox  = el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;margin:6px 0" });
  b.channel_ids.forEach(ch => {
    const isMand = a.mandatory_channel_ids?.includes(ch) || !a.optional_channel_ids?.includes(ch);
    const tag = el("span", { class: "tag" + (isMand ? " green" : " amber") }, isMand ? "Mandatory" : "Optional");
    const row = el("div", { class: "ch-item chip-toggle", style: "min-width:200px" },
      el("div", {}, labels[ch] || ch), tag);
    row.dataset.ch = ch; row.dataset.kind = isMand ? "mandatory" : "optional";
    row.addEventListener("click", () => {
      row.dataset.kind = row.dataset.kind === "mandatory" ? "optional" : "mandatory";
      tag.textContent = row.dataset.kind === "mandatory" ? "Mandatory" : "Optional";
      tag.className = "tag" + (row.dataset.kind === "mandatory" ? " green" : " amber");
    });
    (isMand ? mandBox : optBox).append(row);
  });
  card.append(el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px" },
    el("div", {}, el("h6", { style: "font-size:11px;color:#6B7280;letter-spacing:.1em;margin:0 0 4px" }, "MANDATORY"), mandBox),
    el("div", {}, el("h6", { style: "font-size:11px;color:#6B7280;letter-spacing:.1em;margin:0 0 4px" }, "OPTIONAL"), optBox)
  ));
  msg.append(card);
  msg.append(quickRow([
    { label: "套用影片示範 (6,000,000 / TV+Meta 必上)" },
    { label: "確認 ▶", primary: true }
  ], async (v, o) => {
    if (o.label.startsWith("套用")) {
      bud.value = 6000000;
      card.querySelectorAll(".chip-toggle").forEach(row => {
        const mand = (row.dataset.ch === "tv_advertising" || row.dataset.ch === "meta_video_ads");
        row.dataset.kind = mand ? "mandatory" : "optional";
        const tag = row.querySelector(".tag");
        tag.textContent = mand ? "Mandatory" : "Optional";
        tag.className = "tag" + (mand ? " green" : " amber");
      });
      return;
    }
    const mandatory_channel_ids = [];
    const optional_channel_ids = [];
    card.querySelectorAll(".chip-toggle").forEach(r => {
      (r.dataset.kind === "mandatory" ? mandatory_channel_ids : optional_channel_ids).push(r.dataset.ch);
    });
    try {
      await submit({
        total_budget_twd: parseFloat(bud.value) || 0,
        mandatory_channel_ids, optional_channel_ids
      });
      userSay(`Budget：${(+bud.value).toLocaleString()} · Mandatory：${mandatory_channel_ids.join(", ")||"無"}`);
    } catch (e) { showError(e); }
  }));
}

function renderMinMax(msg) {
  const b = state.session.brief;
  const labels = state.opts.labels || {};
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, "Step 9 · Min / Max per channel (可留空)"));
  const tbl = el("table", { class: "tbl" });
  tbl.append(el("thead", {}, el("tr", {},
    el("th", {}, "Channel"),
    el("th", { class: "num" }, "Min Budget"),
    el("th", { class: "num" }, "Max Budget"),
    el("th", { class: "num" }, "Min Reach %"),
    el("th", { class: "num" }, "Max Freq"))));
  const tb = el("tbody", {});
  const inputs = {};
  b.channel_ids.forEach(ch => {
    inputs[ch] = {};
    ["mb", "xb", "mr", "xf"].forEach(k => { inputs[ch][k] = el("input", { type: "number", style: "width:90px" }); });
    tb.append(el("tr", {},
      el("td", {}, labels[ch] || ch),
      el("td", { class: "num" }, inputs[ch].mb),
      el("td", { class: "num" }, inputs[ch].xb),
      el("td", { class: "num" }, inputs[ch].mr),
      el("td", { class: "num" }, inputs[ch].xf)));
  });
  tbl.append(tb);
  card.append(tbl);
  msg.append(card);
  msg.append(quickRow([
    { label: "全部不設限 (Skip)" },
    { label: "確認 ▶", primary: true }
  ], async (v, o) => {
    if (o.label.startsWith("全部")) {
      try { await submit({ constraints: {} }); userSay("不設 Min/Max 限制"); } catch (e) { showError(e); }
      return;
    }
    const constraints = {};
    b.channel_ids.forEach(ch => {
      const x = inputs[ch];
      const c = {
        min_budget: +x.mb.value || null,
        max_budget: +x.xb.value || null,
        min_reach_pct: +x.mr.value || null,
        max_frequency: +x.xf.value || null
      };
      if (Object.values(c).some(v => v != null)) constraints[ch] = c;
    });
    try { await submit({ constraints }); userSay("Min/Max 設定完成"); } catch (e) { showError(e); }
  }));
}

function renderOptimize(msg) {
  const curve = state.opts.budget_step_curve || [];
  const card = el("div", { class: "card" });
  card.append(el("h5", {}, "🪄 Run Optimization"));
  if (curve.length) {
    card.append(el("h5", { style: "margin-top:8px" }, "Budget sweep preview (10 steps)"));
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Step"),
      el("th", { class: "num" }, "Budget %"),
      el("th", { class: "num" }, "Budget (TWD)"),
      el("th", { class: "num" }, "Net Reach %"),
      el("th", { class: "num" }, "Frequency"),
      el("th", { class: "num" }, "Impressions"))));
    const tb = el("tbody", {});
    curve.forEach(r => tb.append(el("tr", {},
      el("td", {}, r.step),
      el("td", { class: "num" }, r.budget_pct + "%"),
      el("td", { class: "num" }, r.budget_twd.toLocaleString()),
      el("td", { class: "num" }, r.net_reach_pct),
      el("td", { class: "num" }, r.frequency),
      el("td", { class: "num" }, r.total_impressions.toLocaleString()))));
    tbl.append(tb);
    card.append(tbl);
    card.append(el("div", { class: "note" },
      "💡 影片提到「系統目前沒有 budget sweep」，這裡模擬 10 個級距供參考。"));
  }
  msg.append(card);
  msg.append(quickRow([{ label: "執行並 Save plan ▶", primary: true }], async () => {
    try { await submit({}); userSay("執行自動化最佳化"); } catch (e) { showError(e); }
  }));
}

function renderReview(msg) {
  const plan = state.plan;
  const card = el("div", { class: "card" });
  const kind = plan?.kind || (state.mode === "manual" ? "Manual" : "Automatic");
  card.append(el("h5", {}, `✅ Brief 完成 · ${kind} ${plan?.name || ""}`));
  if (plan) {
    [["Total Budget (TWD)", plan.summary.total_budget_twd?.toLocaleString()],
     ["Total Impressions", plan.summary.total_impressions?.toLocaleString()],
     ["Total GRP", plan.summary.total_grp],
     ["Net Reach %", plan.summary.net_reach_pct],
     ["Frequency", plan.summary.frequency]].forEach(([k, v]) =>
      card.append(el("div", { class: "sum-row" }, el("span", {}, k), el("b", {}, v ?? "—"))));
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Channel"),
      el("th", { class: "num" }, "Budget"),
      el("th", { class: "num" }, "Impressions"),
      el("th", { class: "num" }, "GRP"),
      el("th", { class: "num" }, "Net Reach %"),
      el("th", { class: "num" }, "Freq"))));
    const tb = el("tbody", {});
    plan.allocations.forEach(a => tb.append(el("tr", {},
      el("td", {}, a.channel_id),
      el("td", { class: "num" }, a.total_budget_twd.toLocaleString()),
      el("td", { class: "num" }, a.total_impressions.toLocaleString()),
      el("td", { class: "num" }, a.total_grp),
      el("td", { class: "num" }, a.net_reach_pct),
      el("td", { class: "num" }, a.frequency))));
    tbl.append(tb);
    card.append(tbl);
  }
  card.append(el("pre", { class: "code" }, JSON.stringify({
    mode: state.mode,
    brief: state.session.brief,
    plan_id: state.session.plan_id
  }, null, 2)));
  msg.append(card);
  msg.append(quickRow([
    { label: state.mode === "manual" ? "帶著這份 Brief 跑 Automatic Plan ▶" : "帶著這份 Brief 跑 Manual Plan ▶", primary: true },
    { label: "Compare plans ▶", value: "compare" },
    { label: "全新 session" }
  ], async (v, o) => {
    if (o.label.startsWith("Compare")) { openComparePicker(); return; }
    if (o.label.startsWith("帶著")) {
      const target = state.mode === "manual" ? "automatic" : "manual";
      try {
        const res = await api.fork(state.sessionId, target);
        state.mode = target;
        state.sessionId = res.session.id;
        syncModeButtons();
        applyResponse(res);
        scroll.innerHTML = "";
        // res.session.step comes from the server but is ultimately derived
        // from a StepKey enum — escape anyway as defense-in-depth.
        const priorMode = state.mode === "automatic" ? "Manual" : "Automatic";
        botSay(`🔗 已從 <b>${escapeHTML(priorMode)}</b> session 帶入 Brief。直接從 <b>${escapeHTML(res.session.step)}</b> 開始。`);
        renderSidebar();
        renderSummary();
        renderStep();
      } catch (e) { showError(e); }
    } else {
      startSession();
    }
  }));
}

/* ---------- Flow helpers ---------- */
async function submit(payload) {
  const res = await api.advance(state.sessionId, payload);
  applyResponse(res);
  renderStep();
  renderSidebar();
  renderSummary();
}
function applyResponse(res) {
  state.session = res.session;
  state.prompt = res.prompt;
  state.opts = res.available_options;
  state.warnings = res.warnings;
  state.plan = res.plan;
  localStorage.setItem("ccs_session_" + state.mode, state.sessionId);
}
function showError(e) {
  console.error(e);
  const msg = botSay("");
  msg.append(el("div", { class: "err-box" }, "⚠️ " + (e.message || "Unknown error")));
}

async function startSession() {
  scroll.innerHTML = "";
  botSay("👋 你好！我是 <b>CCS Planning Agent</b>，現在使用 <b>" +
    (state.mode === "manual" ? "Manual" : "Automatic") + "</b> 模式。建立 session…");
  const res = await api.create(state.mode);
  state.sessionId = res.session.id;
  applyResponse(res);
  renderSidebar();
  renderSummary();
  renderStep();
}

async function resumeOrStart() {
  const saved = localStorage.getItem("ccs_session_" + state.mode);
  if (saved) {
    try {
      const res = await api.get(saved);
      state.sessionId = res.session.id;
      applyResponse(res);
      renderSidebar();
      renderSummary();
      // ``saved`` originates from localStorage, which a local attacker can
      // poison. Escape before inlining into HTML.
      botSay("⏪ 已載入上次的 session: <code>" + escapeHTML(saved) + "</code>");
      renderStep();
      return;
    } catch {
      localStorage.removeItem("ccs_session_" + state.mode);
    }
  }
  startSession();
}

/* ---------- Composer ---------- */
async function sendText() {
  const t = $("#input").value.trim();
  if (!t) return;
  $("#input").value = "";
  if (t === "/skip") { userSay(t); await submit({ action: "skip" }); return; }
  if (t === "/back") { userSay(t); await submit({ action: "back" }); return; }
  if (t === "/show") {
    userSay(t);
    // Brief values may contain user input (e.g. project_name). Build a <pre>
    // node and set textContent so any embedded HTML renders literally.
    const msg = botSay("");
    const pre = el("pre", { class: "code" });
    pre.textContent = JSON.stringify(state.session.brief, null, 2);
    msg.append(pre);
    return;
  }
  userSay(t);
  botSay("✍️ 請使用上方卡片內的欄位完成這一步。");
}
$("#send").addEventListener("click", sendText);
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendText(); });

function syncModeButtons() {
  $("#btnManual").classList.toggle("active", state.mode === "manual");
  $("#btnAuto").classList.toggle("active", state.mode === "automatic");
}
$("#btnManual").addEventListener("click", () => { state.mode = "manual"; syncModeButtons(); resumeOrStart(); });
$("#btnAuto").addEventListener("click",   () => { state.mode = "automatic"; syncModeButtons(); resumeOrStart(); });
$("#btnReset").addEventListener("click",  () => {
  localStorage.removeItem("ccs_session_" + state.mode);
  startSession();
});

/* ---------- Compare plans (FR-13..FR-14) ---------- */

/** Compare requires ≥ 2 saved plans. When the user has 0 or 1, give them
 *  a one-click path to produce the second one — either fork the current
 *  session into the opposite mode (most common case) or start a fresh
 *  session when there's no plan at all. */
function _renderCompareNeedsSecondPlan(plans) {
  const card = el("div", { class: "card compare-view full" });
  card.append(el("h5", {}, "📊 想要比較 plans"));

  if (plans.length === 0) {
    card.append(el("div", { class: "note" },
      "目前還沒有任何 saved plan。先把一個 Manual 或 Automatic session 跑到最後 (Review step 按 Save) 再回來比較。"));
    const btns = quickRow([
      { label: "+ Manual session", primary: true, value: "manual" },
      { label: "+ Automatic session", value: "automatic" },
    ], async (mode) => {
      const projId = state.session?.brief?.project_id
        || (await api.listProjects())[0]?.id;
      if (projId) await startNewSessionInProject(projId, mode);
      else botSay("⚠️ 找不到可用的 project，先去 🏠 Home 建一個。");
    });
    card.append(btns);
    const msg = botSay("⚠️ 沒有 saved plans 可以比較。");
    msg.append(card);
    return;
  }

  const only = plans[0];
  const otherMode = only.kind === "Manual" ? "automatic" : "manual";
  const otherLabel = only.kind === "Manual" ? "Automatic" : "Manual";
  card.append(el("div", {
    class: "note",
    html: `你目前只有 <b>${escapeHTML(only.name)} · ${escapeHTML(only.kind)}</b>。Compare 至少需要 2 個。`
        + ` 最快的做法是把同一份 Brief fork 成 <b>${escapeHTML(otherLabel)}</b>，讓 CCS Planner 幫你產第二個 plan。`
  }));

  const btns = quickRow([
    { label: `🔄 Fork this brief into ${otherLabel} ▶`, primary: true, value: "fork" },
    { label: "全新 session", value: "new" },
  ], async (v) => {
    if (v === "fork") {
      // fork from whichever session owns the existing plan.
      try {
        const res = await api.fork(only.brief_id, otherMode);
        state.mode = otherMode;
        state.sessionId = res.session.id;
        applyResponse(res);
        syncModeButtons();
        scroll.innerHTML = "";
        botSay(`🔗 Fork 完成，進入 <b>${otherLabel}</b> 流程。跑完後再點 📊 Compare 就能對比兩份 plan。`);
        renderSidebar();
        renderSummary();
        renderStep();
      } catch (err) { showError(err); }
    } else {
      renderProjects();
    }
  });
  card.append(btns);

  const msg = botSay("⚠️ 需要 2 個 plans 才能比較 — 一鍵幫你補：");
  msg.append(card);
}


async function openComparePicker() {
  let plans;
  try {
    const briefId = state.session?.brief?.id || state.session?.id;
    plans = await api.listPlans(briefId);
    if (!plans || plans.length < 2) plans = await api.listPlans();
  } catch (e) { showError(e); return; }

  // Not enough plans: instead of a dead-end warning, invite the user to
  // build the OTHER mode's plan. That's the whole point of Compare.
  if (!plans || plans.length < 2) {
    _renderCompareNeedsSecondPlan(plans || []);
    return;
  }

  const backdrop = el("div", { class: "modal-backdrop" });
  const modal = el("div", { class: "modal" });
  const body = el("div", { class: "body" });
  plans.forEach((p, i) => {
    const row = el("label", { class: "plan-row" },
      el("input", { type: "checkbox", value: p.id, ...(i < 2 ? { checked: "" } : {}) }),
      el("div", {}, `${p.name} · ${p.kind}`,
        el("div", { class: "meta" },
          `budget ${Math.round(p.summary?.total_budget_twd || 0).toLocaleString()} · `
          + `reach ${(p.summary?.net_reach_pct || 0).toFixed(1)}%`))
    );
    body.append(row);
  });

  const confirmBtn = el("button", { class: "primary" }, "比較選定的 plans ▶");
  const cancelBtn = el("button", {}, "取消");
  const close = () => backdrop.remove();
  cancelBtn.addEventListener("click", close);
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
  });
  confirmBtn.addEventListener("click", async () => {
    const ids = [...body.querySelectorAll("input:checked")].map(x => x.value);
    if (ids.length < 2) return;
    close();
    await renderCompare(ids);
  });

  modal.append(
    el("header", {}, el("b", {}, "Compare plans"),
      el("button", { onclick: close }, "✕")),
    body,
    el("div", { class: "foot" }, cancelBtn, confirmBtn)
  );
  backdrop.append(modal);
  document.body.append(backdrop);
}

async function renderCompare(planIds) {
  const r = await apiFetch("/api/plans/compare", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(planIds),
  });
  if (!r.ok) { showError(new Error(await r.text())); return; }
  const payload = await r.json();

  const section = el("div", { class: "card compare-view full" });
  section.append(el("h5", {}, "📊 Plan comparison"));

  const hero = el("div", { class: "hero" });
  const hm = [
    ["Budget (TWD)", "total_budget_twd", v => Math.round(v).toLocaleString(), false],
    ["Impressions", "total_impressions", v => Math.round(v).toLocaleString(), false],
    ["Net Reach %", "net_reach_pct", v => v.toFixed(2) + "%", true],
    ["Frequency", "frequency", v => v.toFixed(2), true],
    ["Total GRP", "total_grp", v => v.toFixed(2), true],
  ];
  hm.forEach(([label, key, fmt, higherIsBetter]) => {
    const v0 = payload.plans[0].summary[key] ?? 0;
    const v1 = payload.plans[payload.plans.length - 1].summary[key] ?? 0;
    const diff = v1 - v0;
    const goodDirection = (higherIsBetter && diff > 0) || (!higherIsBetter && diff < 0);
    const sign = diff > 0 ? "+" : "";
    hero.append(el("div", { class: "cell" },
      el("small", {}, label),
      el("b", {}, fmt(v1)),
      el("div", {
        class: "delta " + (diff === 0 ? "" : goodDirection ? "up" : "down"),
      }, diff === 0 ? "—" : `${sign}${fmt(diff)} vs ${payload.plans[0].name}`)
    ));
  });
  section.append(hero);

  const grid = el("div", { class: "grid" });
  const summaryCanvas = el("canvas", { id: "chart-summary" });
  const budgetCanvas = el("canvas", { id: "chart-budget" });
  const reachCanvas = el("canvas", { id: "chart-reach" });
  const freqCanvas = el("canvas", { id: "chart-frequency" });
  const weeklyCanvas = el("canvas", { id: "chart-weekly", style: "max-height:260px" });

  grid.append(
    el("div", { class: "card" }, el("h5", {}, "Performance summary"), summaryCanvas),
    el("div", { class: "card" }, el("h5", {}, "Budget per channel"), budgetCanvas),
    el("div", { class: "card" }, el("h5", {}, "Reach / Attentive / Engagement"), reachCanvas),
    el("div", { class: "card" }, el("h5", {}, "Frequency distribution (1+ … 10+)"), freqCanvas),
    el("div", { class: "card full" }, el("h5", {}, "Weekly GRP"), weeklyCanvas),
  );
  section.append(grid);

  const dupWrap = el("div", { class: "card full" });
  dupWrap.append(el("h5", {}, "Duplication & Exclusivity"));
  const dupTbl = el("table", { class: "tbl" });
  const dupHead = el("tr", {}, el("th", {}, "Plan"), el("th", {}, "Channel"),
    el("th", { class: "num" }, "Net Reach %"),
    el("th", { class: "num" }, "Exclusivity %"),
    el("th", { class: "num" }, "Duplication %"));
  dupTbl.append(el("thead", {}, dupHead));
  const dupBody = el("tbody", {});
  payload.plans.forEach(p => {
    (p.allocations || []).forEach(a => {
      const d = p.duplication?.[a.channel_id] || { exclusivity_pct: 0, duplication_pct: 0 };
      dupBody.append(el("tr", {},
        el("td", {}, p.name),
        el("td", {}, a.channel_id),
        el("td", { class: "num" }, (a.net_reach_pct || 0).toFixed(2)),
        el("td", { class: "num" }, d.exclusivity_pct.toFixed(2)),
        el("td", { class: "num" }, d.duplication_pct.toFixed(2))
      ));
    });
  });
  dupTbl.append(dupBody);
  dupWrap.append(dupTbl);

  const msg = botSay("🔍 Comparison ready —");
  msg.append(section);
  msg.append(dupWrap);

  try {
    const Chart = await loadChartLib();
    if (!Chart) throw new Error("charts unavailable");
    drawSummaryChart(Chart, summaryCanvas, payload);
    drawBudgetChart(Chart, budgetCanvas, payload);
    drawReachChart(Chart, reachCanvas, payload);
    drawFrequencyChart(Chart, freqCanvas, payload);
    drawWeeklyChart(Chart, weeklyCanvas, payload);
  } catch (err) {
    console.warn(err);
    const fallback = el("div", { class: "fallback" },
      "⚠️ charts unavailable — Chart.js CDN 無法載入，但表格仍可使用。");
    section.insertBefore(fallback, hero);
  }
}

function drawSummaryChart(Chart, canvas, payload) {
  const labels = ["Net Reach", "Attentive", "Engagement", "Frequency ×10", "Brand Consid"];
  const datasets = payload.plans.map((p, i) => ({
    label: p.name,
    backgroundColor: colourFor(p.name, i),
    data: [
      p.summary.net_reach_pct || 0,
      (p.summary.net_reach_pct || 0) * 0.7,
      (p.summary.net_reach_pct || 0) * 0.55,
      (p.summary.frequency || 0) * 10,
      p.summary.brand_consideration_pct || 0,
    ],
  }));
  new Chart(canvas, {
    type: "bar", data: { labels, datasets },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });
}

function drawBudgetChart(Chart, canvas, payload) {
  const channels = new Set();
  payload.plans.forEach(p => p.allocations.forEach(a => channels.add(a.channel_id)));
  const channelList = [...channels];
  const datasets = channelList.map((ch, i) => ({
    label: ch,
    backgroundColor: colourFor(ch, i),
    data: payload.plans.map(p => {
      const a = p.allocations.find(x => x.channel_id === ch);
      return a ? a.total_budget_twd : 0;
    }),
  }));
  new Chart(canvas, {
    type: "bar",
    data: { labels: payload.plans.map(p => p.name), datasets },
    options: {
      indexAxis: "y", responsive: true,
      scales: { x: { stacked: true }, y: { stacked: true } },
      plugins: { legend: { position: "bottom" } },
    },
  });
}

function drawReachChart(Chart, canvas, payload) {
  const labels = payload.plans.map(p => p.name);
  const datasets = [
    { label: "Net Reach %", backgroundColor: PALETTE[0],
      data: payload.plans.map(p => p.summary.net_reach_pct || 0) },
    { label: "Attitude %", backgroundColor: PALETTE[1],
      data: payload.plans.map(p => p.summary.attitude_measures_pct || 0) },
    { label: "Brand Consid %", backgroundColor: PALETTE[2],
      data: payload.plans.map(p => p.summary.brand_consideration_pct || 0) },
    { label: "Knowledge %", backgroundColor: PALETTE[3],
      data: payload.plans.map(p => p.summary.brand_knowledge_scores_pct || 0) },
  ];
  new Chart(canvas, {
    type: "bar", data: { labels, datasets },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });
}

function drawFrequencyChart(Chart, canvas, payload) {
  const labels = ["1+", "2+", "3+", "4+", "5+", "6+", "7+", "8+", "9+", "10+"];
  const datasets = payload.plans.map((p, i) => ({
    label: p.name,
    borderColor: colourFor(p.name, i),
    backgroundColor: colourFor(p.name, i) + "33",
    fill: false, tension: 0.3,
    data: (p.frequency_distribution || []).map(row => row.reach_pct),
  }));
  new Chart(canvas, {
    type: "line", data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { position: "bottom" } },
      scales: { y: { min: 0, max: 100 } },
    },
  });
}

function drawWeeklyChart(Chart, canvas, payload) {
  const labels = (payload.plans[0].weekly_grp || []).map(w => `W${w.week}`);
  const datasets = payload.plans.map((p, i) => ({
    label: p.name,
    borderColor: colourFor(p.name, i),
    backgroundColor: colourFor(p.name, i) + "22",
    fill: true, tension: 0.25,
    data: (p.weekly_grp || []).map(w => w.grp),
  }));
  new Chart(canvas, {
    type: "line", data: { labels, datasets },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });
}

/* ---------- v4: Login + Home + Project detail + History ---------- */

function renderLogin() {
  scroll.innerHTML = "";
  const msg = botSay("👤 請輸入 API key 以進入 CCS Planning Agent。");
  const wrap = el("div", { class: "card", style: "max-width:460px" });
  const input = el("input", { id: "apikey", type: "password", placeholder: "paste X-API-Key…",
    style: "width:100%;border:1px solid #E5E7EB;border-radius:8px;padding:10px;font:inherit" });
  wrap.append(
    el("h5", {}, "Login"),
    el("div", { class: "note" }, "Admin: 用 CCS_ADMIN_KEY 啟動時設定的 key。其他人: admin 在 /api/users 產生後發給你的一次性 key。"),
    el("div", { style: "margin:10px 0" }, input),
    quickRow([{ label: "登入 ▶", primary: true }], () => {
      const k = input.value.trim();
      if (!k) return;
      setApiKey(k);
      bootApp();
    })
  );
  msg.append(wrap);
}

function showLoginPrompt() { renderLogin(); }

async function renderProjects() {
  scroll.innerHTML = "";
  const msg = botSay("🏠 <b>Home</b> — 你的專案清單：");
  const list = el("div", { class: "card compare-view full" });
  const header = el("div", { style: "display:flex;align-items:center;margin-bottom:8px" },
    el("h5", { style: "margin:0" }, "Projects"),
    el("div", { style: "margin-left:auto;display:flex;gap:8px" },
      el("input", { id: "newproj", placeholder: "new project name", style: "border:1px solid #E5E7EB;border-radius:8px;padding:6px 10px;font:inherit" }),
      el("button", {
        class: "",
        style: "border:0;background:#111827;color:#fff;padding:7px 14px;border-radius:999px;cursor:pointer",
        onclick: async () => {
          const name = $("#newproj").value.trim();
          if (!name) return;
          await api.createProject(name);
          renderProjects();
        },
      }, "+ New project")
    )
  );
  list.append(header);
  const grid = el("div", { class: "grid" });
  try {
    const projects = await api.listProjects();
    if (!projects.length) {
      grid.append(el("div", { class: "empty full" }, "尚無專案。輸入名稱按 + New project 建立第一個。"));
    } else {
      projects.forEach(p => {
        const card = el("div", { class: "card", style: "cursor:pointer", onclick: () => renderProjectDetail(p.id) },
          el("h5", {}, p.name),
          el("div", { class: "sum-row" }, el("span", {}, "Sessions"), el("b", {}, String(p.session_count))),
          el("div", { class: "sum-row" }, el("span", {}, "Plans"), el("b", {}, String(p.plan_count)))
        );
        grid.append(card);
      });
    }
  } catch (err) { showError(err); }
  list.append(grid);
  msg.append(list);
}

async function renderProjectDetail(projectId) {
  scroll.innerHTML = "";
  const msg = botSay("📁 Project detail");
  const wrap = el("div", { class: "card compare-view full" });
  try {
    const [proj, sessions, plans] = await Promise.all([
      apiFetch(`/api/projects/${projectId}`).then(r => r.json()),
      api.projectSessions(projectId),
      api.projectPlans(projectId),
    ]);
    wrap.append(
      el("h5", {}, `📁 ${proj.name}`),
      el("div", { style: "display:flex;gap:8px;margin:8px 0" },
        el("button", { style: "padding:6px 12px;border-radius:999px;border:1px solid #E5E7EB;background:#fff;cursor:pointer",
          onclick: () => renderProjects() }, "← All projects"),
        el("button", { style: "padding:6px 12px;border-radius:999px;border:0;background:#111827;color:#fff;cursor:pointer",
          onclick: () => startNewSessionInProject(projectId, "manual") }, "+ Manual session"),
        el("button", { style: "padding:6px 12px;border-radius:999px;border:0;background:#8B5CF6;color:#fff;cursor:pointer",
          onclick: () => startNewSessionInProject(projectId, "automatic") }, "+ Automatic session"),
      )
    );

    const sTbl = el("table", { class: "tbl" });
    sTbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Session"), el("th", {}, "Mode"), el("th", {}, "Step"), el("th", {}, "Open"))));
    const sBody = el("tbody", {});
    sessions.forEach(s => {
      sBody.append(el("tr", {},
        el("td", {}, s.id),
        el("td", {}, s.mode),
        el("td", {}, s.step),
        el("td", {}, el("button", {
          style: "padding:4px 10px;border-radius:999px;border:1px solid #E5E7EB;background:#fff;cursor:pointer",
          onclick: () => resumeSession(s.id, s.mode),
        }, "Resume ▶"))
      ));
    });
    sTbl.append(sBody);
    wrap.append(el("h5", { style: "margin-top:12px" }, `Sessions (${sessions.length})`), sTbl);

    const pTbl = el("table", { class: "tbl" });
    pTbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Plan"), el("th", {}, "Kind"),
      el("th", { class: "num" }, "Budget"),
      el("th", { class: "num" }, "Reach %"))));
    const pBody = el("tbody", {});
    plans.forEach(p => pBody.append(el("tr", {},
      el("td", {}, p.name),
      el("td", {}, p.kind),
      el("td", { class: "num" }, Math.round(p.summary?.total_budget_twd || 0).toLocaleString()),
      el("td", { class: "num" }, (p.summary?.net_reach_pct || 0).toFixed(2))
    )));
    pTbl.append(pBody);
    wrap.append(el("h5", { style: "margin-top:12px" }, `Plans (${plans.length})`), pTbl);

    if (plans.length >= 2) {
      wrap.append(quickRow(
        [{ label: "📊 Compare these plans", primary: true }],
        () => renderCompare(plans.map(p => p.id))
      ));
    }
  } catch (err) { showError(err); }
  msg.append(wrap);
}

async function startNewSessionInProject(projectId, mode) {
  try {
    const res = await apiFetch("/api/sessions", {
      method: "POST", body: JSON.stringify({ mode, project_id: projectId })
    }).then(r => r.json());
    state.mode = mode;
    state.sessionId = res.session.id;
    applyResponse(res);
    syncModeButtons();
    scroll.innerHTML = "";
    botSay(`🚀 開啟新 session in project. (id: <code>${escapeHTML(res.session.id)}</code>)`);
    renderSidebar();
    renderSummary();
    renderStep();
  } catch (err) { showError(err); }
}

async function resumeSession(sessionId, mode) {
  try {
    const res = await api.get(sessionId);
    state.mode = mode;
    state.sessionId = sessionId;
    applyResponse(res);
    syncModeButtons();
    scroll.innerHTML = "";
    botSay(`⏪ Resume session <code>${escapeHTML(sessionId)}</code>`);
    renderSidebar();
    renderSummary();
    renderStep();
  } catch (err) { showError(err); }
}

async function renderHistory(sessionId) {
  const sid = sessionId || state.sessionId;
  if (!sid) { botSay("⚠️ 沒有 session 可顯示 history。"); return; }
  try {
    const turns = await api.conversation(sid);
    const msg = botSay(`📜 Conversation history — ${turns.length} turns`);
    const wrap = el("div", { class: "card full" });
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "#"), el("th", {}, "Step"), el("th", {}, "When"),
      el("th", {}, "Payload"), el("th", {}, "Brief snapshot"))));
    const body = el("tbody", {});
    turns.forEach(t => {
      const when = new Date(t.ts * 1000).toLocaleString();
      const payloadPre = el("pre", { style: "font-size:11px;max-width:260px;white-space:pre-wrap;margin:0" });
      payloadPre.textContent = JSON.stringify(t.payload, null, 2);
      const snapPre = el("pre", { style: "font-size:11px;max-width:320px;white-space:pre-wrap;margin:0;max-height:140px;overflow:auto" });
      snapPre.textContent = JSON.stringify(t.brief_snapshot, null, 2);
      body.append(el("tr", {},
        el("td", {}, String(t.turn_index)),
        el("td", {}, t.step),
        el("td", {}, when),
        el("td", {}, payloadPre),
        el("td", {}, snapPre),
      ));
    });
    tbl.append(body);
    wrap.append(tbl);
    msg.append(wrap);
  } catch (err) { showError(err); }
}

/* ---------- Users tab (v5, admin-only) ---------- */

async function renderUsers() {
  // Guard: only admins should see this. The backend enforces is_admin
  // regardless, so failing open here would just show 403-messages, but it's
  // cleaner to bail early.
  const me = await api.me().catch(() => null);
  if (!me || !me.is_admin) {
    botSay("⚠️ Users 管理僅限 admin。");
    return;
  }

  scroll.innerHTML = "";
  const msg = botSay("👥 <b>Users</b> — 管理帳號與 API key");

  const card = el("div", { class: "card compare-view full" });

  // Invite-user form
  const row = el("div", { style: "display:flex;gap:8px;margin-bottom:10px" });
  const nameInp = el("input", { id: "new-user-name", placeholder: "new user name",
    style: "flex:1;border:1px solid #E5E7EB;border-radius:8px;padding:8px 10px;font:inherit" });
  const adminChk = el("label", { style: "display:inline-flex;align-items:center;gap:6px;font-size:13px" },
    el("input", { type: "checkbox", id: "new-user-admin" }), "admin");
  const inviteBtn = el("button", {
    style: "padding:8px 14px;border-radius:999px;border:0;background:#111827;color:#fff;cursor:pointer",
    onclick: async () => {
      const name = nameInp.value.trim();
      if (!name) return;
      try {
        const body = { name, is_admin: document.getElementById("new-user-admin").checked };
        const res = await apiFetch("/api/users", { method: "POST", body: JSON.stringify(body) });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        _showOneTimeKey(data.user.name, data.api_key);
        renderUsers();
      } catch (e) { showError(e); }
    }
  }, "+ Invite user");
  row.append(nameInp, adminChk, inviteBtn);
  card.append(row);

  // Users table
  try {
    const users = await apiFetch("/api/users").then(r => r.json());
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Name"), el("th", {}, "Role"), el("th", {}, "Active"),
      el("th", {}, "Created"), el("th", {}, "Actions"))));
    const body = el("tbody", {});
    users.forEach(u => {
      const created = new Date(u.created_at * 1000).toLocaleDateString();
      const roleTag = el("span", { class: "tag " + (u.is_admin ? "green" : "") },
        u.is_admin ? "admin" : "user");
      const activeTag = el("span", { class: "tag " + (u.is_active ? "green" : "red") },
        u.is_active ? "active" : "disabled");

      const actions = el("div", { style: "display:flex;gap:6px" });
      if (u.id !== me.id) {
        const toggleBtn = el("button", {
          style: "padding:3px 8px;border-radius:6px;border:1px solid #E5E7EB;background:#fff;cursor:pointer;font-size:12px",
          onclick: async () => {
            try {
              // Using literal URL paths (not a computed suffix) so they
              // appear in greppable form for static-analysis tests.
              const url = u.is_active
                ? `/api/users/${u.id}/disable`
                : `/api/users/${u.id}/enable`;
              const r = await apiFetch(url, { method: "POST" });
              if (!r.ok) throw new Error(await r.text());
              renderUsers();
            } catch (e) { showError(e); }
          }
        }, u.is_active ? "Disable" : "Enable");
        actions.append(toggleBtn);
      }
      const rotateBtn = el("button", {
        style: "padding:3px 8px;border-radius:6px;border:1px solid #E5E7EB;background:#fff;cursor:pointer;font-size:12px",
        onclick: async () => {
          if (!confirm(`Rotate key for ${u.name}? Their current key will stop working immediately.`)) return;
          try {
            const r = await apiFetch(`/api/users/${u.id}/rotate`, { method: "POST" });
            if (!r.ok) throw new Error(await r.text());
            const data = await r.json();
            _showOneTimeKey(u.name, data.api_key, "rotated");
          } catch (e) { showError(e); }
        }
      }, "Rotate key");
      actions.append(rotateBtn);

      body.append(el("tr", {},
        el("td", {}, u.name),
        el("td", {}, roleTag),
        el("td", {}, activeTag),
        el("td", {}, created),
        el("td", {}, actions)
      ));
    });
    tbl.append(body);
    card.append(tbl);
  } catch (err) { showError(err); }

  msg.append(card);
}

function _showOneTimeKey(name, key, action = "created") {
  const backdrop = el("div", { class: "modal-backdrop" });
  const modal = el("div", { class: "modal" });
  const body = el("div", { class: "body" },
    el("div", { class: "note" }, `One-time key for ${name} (${action}) — copy now, you won't see it again.`),
    el("pre", { class: "code", style: "user-select:all" })
  );
  body.querySelector("pre").textContent = key;
  const close = () => backdrop.remove();
  const copyBtn = el("button", { class: "primary",
    onclick: () => { navigator.clipboard.writeText(key); copyBtn.textContent = "Copied ✓"; } }, "Copy");
  modal.append(
    el("header", {}, el("b", {}, "🔑 API key"), el("button", { onclick: close }, "✕")),
    body,
    el("div", { class: "foot" }, copyBtn, el("button", { onclick: close }, "Done"))
  );
  backdrop.append(modal);
  document.body.append(backdrop);
}

/* ---------- Boot (v4) ---------- */

async function bootApp() {
  const h = await api.health().catch(() => null);
  const el2 = $("#apiIndicator");
  const st = $("#apiStatus");
  if (h) { el2.classList.add("ok"); st.textContent = "ok · v" + h.version; }
  else   { el2.classList.add("err"); st.textContent = "unreachable"; }
  if (!getApiKey()) { showLoginPrompt(); return; }
  const me = await api.me().catch(() => null);
  if (!me) { setApiKey(""); showLoginPrompt(); return; }
  // Reveal the 👥 Users button only for admins (v5).
  const usersBtn = document.getElementById("btnUsers");
  if (usersBtn) usersBtn.style.display = me.is_admin ? "" : "none";
  botSay(`👋 Hi <b>${escapeHTML(me.name)}</b>${me.is_admin ? " (admin)" : ""} — 載入專案…`);
  renderProjects();
}

/* Install #navHome click (delegated) */
document.addEventListener("click", (e) => {
  const t = e.target;
  if (!(t instanceof HTMLElement)) return;
  if (t.id === "btnHome") { renderProjects(); }
  if (t.id === "btnHistory") { renderHistory(); }
  if (t.id === "btnUsers") { renderUsers(); }
  if (t.id === "btnLogout") { setApiKey(""); bootApp(); }
});

bootApp();
