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

/* ========================================================================
 * v6 · Actuals capture, report & recommend-fill banner (PRD v6 · FR-27..33)
 * ====================================================================== */

const RECOMMEND_FILL_BANNER = {
  headline: "填好這兩項，之後預估會越來越準",
  body:
    "精準的成效推估，建議一定要填：Channel Calibration 與 Penetration Adjustment。" +
    "填完後系統會記住你這家 client × target 的實際表現，之後預估會越來越準。",
  cta: "立即填寫",
};

/* Called from renderChannels (CHANNELS step). Shows a sticky yellow
 * banner when `(client_id × target_id)` has no prior actuals. Dismissing
 * is session-local only. */
async function maybeShowRecommendFillBanner(card) {
  const b = state.session?.brief;
  if (!b?.client_id || !(b.target_ids || []).length) return;
  if (state.bannerDismissed) return;
  const target = b.target_ids[0];
  try {
    const r = await apiFetch(
      `/api/calibration/coverage?client_id=${encodeURIComponent(b.client_id)}` +
      `&target_id=${encodeURIComponent(target)}`
    );
    const body = await r.json();
    if (body.has_history) return;
  } catch (_) { return; }

  const banner = el("div", {
    class: "card",
    style: "background:#FFFBEB;border:1px solid #F59E0B;margin:8px 0;padding:10px 12px",
  });
  banner.append(
    el("div", { style: "font-weight:700;color:#92400E;margin-bottom:4px" },
       RECOMMEND_FILL_BANNER.headline),
    el("div", { style: "font-size:13px;color:#7C2D12;margin-bottom:6px" },
       RECOMMEND_FILL_BANNER.body),
    el("div", { style: "display:flex;gap:8px" },
      el("button", {
        style: "padding:6px 12px;border-radius:999px;border:0;background:#F59E0B;color:#fff;cursor:pointer",
        onclick: () => { alert("此版本請先完成 Brief，並在 Plan 完成後於 Project Detail 點 '📊 記錄成效'。"); },
      }, RECOMMEND_FILL_BANNER.cta),
      el("button", {
        style: "padding:6px 12px;border-radius:999px;border:1px solid #E5E7EB;background:#fff;cursor:pointer",
        onclick: () => { state.bannerDismissed = true; banner.remove(); },
      }, "本次先跳過"),
    ),
  );
  card.parentNode?.insertBefore(banner, card);
}

/* ---------- Actuals modal ---------- */

function _actualsCellInputs(prefix, initial) {
  const fields = [
    ["spend_twd", "花費 (TWD)"],
    ["impressions", "Impressions"],
    ["cpm_twd", "CPM"],
    ["net_reach_pct", "Net Reach %"],
    ["frequency", "Frequency"],
    ["penetration_pct", "Penetration %"],
    ["buying_audience_000", "Buying Audience (千人)"],
  ];
  const row = el("div", { style: "display:grid;grid-template-columns:repeat(7,minmax(80px,1fr));gap:6px" });
  const inputs = {};
  fields.forEach(([k, label]) => {
    const id = `${prefix}-${k}`;
    const input = el("input", {
      id, type: "number", step: "any",
      value: initial && initial[k] != null ? String(initial[k]) : "",
      style: "width:100%;padding:3px 6px;border:1px solid #E5E7EB;border-radius:6px;font-size:12px",
    });
    inputs[k] = input;
    row.append(el("div", {},
      el("div", { style: "font-size:10px;color:#6B7280" }, label),
      input));
  });
  return { row, inputs };
}

async function openActualsModal(plan) {
  const existing = await apiFetch(`/api/plans/${plan.id}/actuals`).then(r => r.json());
  const byWeek = {};
  let finalRec = null;
  existing.forEach(rec => {
    if (rec.scope === "FINAL") finalRec = rec;
    else if (rec.scope === "WEEKLY" && rec.period_week) byWeek[rec.period_week] = rec;
  });

  const overlay = el("div", {
    style: "position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:1000;" +
           "display:flex;align-items:center;justify-content:center",
  });
  const modal = el("div", {
    style: "background:#fff;border-radius:12px;padding:20px;width:min(980px,92vw);" +
           "max-height:88vh;overflow:auto",
  });

  modal.append(
    el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:10px" },
      el("h3", { style: "margin:0" }, `📊 記錄成效 · ${plan.name}`),
      el("button", {
        style: "border:0;background:#F3F4F6;padding:4px 10px;border-radius:999px;cursor:pointer",
        onclick: () => overlay.remove(),
      }, "✕"))
  );

  // Tab switcher
  const tabWeekly = el("button", {
    style: "padding:6px 14px;border:0;border-radius:999px;background:#111827;color:#fff;cursor:pointer",
  }, "週週補");
  const tabFinal = el("button", {
    style: "padding:6px 14px;border:0;border-radius:999px;background:#F3F4F6;color:#111827;cursor:pointer",
  }, "最終結算");
  const tabs = el("div", { style: "display:flex;gap:8px;margin-bottom:10px" }, tabWeekly, tabFinal);
  modal.append(tabs);

  const weeklyView = el("div", {});
  const finalView = el("div", { style: "display:none" });

  // Weekly view — one row per channel × week, grouped by week
  const channels = plan.allocations.map(a => a.channel_id);
  const weekCount = plan.allocations[0]?.weeks?.length || 4;
  const weeklyInputs = {};  // weeklyInputs[week][ch] = {inputs:{...}}
  for (let w = 1; w <= weekCount; w++) {
    weeklyInputs[w] = {};
    const block = el("details", { ...(byWeek[w] ? { open: "" } : {}), style: "border:1px solid #E5E7EB;border-radius:8px;padding:8px;margin-bottom:8px" });
    block.append(el("summary", { style: "font-weight:600;cursor:pointer" }, `Week ${w}`));
    channels.forEach(ch => {
      const init = byWeek[w]?.per_channel?.[ch];
      const { row, inputs } = _actualsCellInputs(`w${w}-${ch}`, init);
      weeklyInputs[w][ch] = inputs;
      block.append(
        el("div", { style: "margin-top:6px;font-size:12px;color:#374151" }, ch),
        row
      );
    });
    weeklyView.append(block);
  }

  // Final view
  const finalInputs = {};
  channels.forEach(ch => {
    const init = finalRec?.per_channel?.[ch];
    const { row, inputs } = _actualsCellInputs(`final-${ch}`, init);
    finalInputs[ch] = inputs;
    finalView.append(
      el("div", { style: "margin-top:6px;font-size:13px;color:#111827;font-weight:600" }, ch),
      row
    );
  });
  const aggregateBtn = el("button", {
    style: "margin-top:12px;padding:8px 14px;border:1px solid #8B5CF6;background:#fff;color:#8B5CF6;border-radius:999px;cursor:pointer",
    onclick: () => {
      // Aggregate weekly → suggest final
      channels.forEach(ch => {
        let sumSpend = 0, sumImpr = 0, cntCpm = 0, cpmAcc = 0, reachAcc = 0, penAcc = 0, cntRatio = 0;
        for (let w = 1; w <= weekCount; w++) {
          const ins = weeklyInputs[w][ch];
          const sp = Number(ins.spend_twd.value || 0);
          const im = Number(ins.impressions.value || 0);
          const cpm = Number(ins.cpm_twd.value || 0);
          const reach = Number(ins.net_reach_pct.value || 0);
          const pen = Number(ins.penetration_pct.value || 0);
          if (sp || im) { sumSpend += sp; sumImpr += im; }
          if (cpm) { cpmAcc += cpm; cntCpm++; }
          if (reach || pen) { reachAcc += reach; penAcc += pen; cntRatio++; }
        }
        finalInputs[ch].spend_twd.value = sumSpend.toFixed(0);
        finalInputs[ch].impressions.value = sumImpr.toFixed(0);
        finalInputs[ch].cpm_twd.value = cntCpm ? (cpmAcc / cntCpm).toFixed(2) : (sumImpr ? (sumSpend / sumImpr * 1000).toFixed(2) : "");
        finalInputs[ch].net_reach_pct.value = cntRatio ? (reachAcc / cntRatio).toFixed(2) : "";
        finalInputs[ch].penetration_pct.value = cntRatio ? (penAcc / cntRatio).toFixed(2) : "";
      });
      botSay("已依週數據試算最終結算，請檢查後再存。");
    },
  }, "用週數據試算最終結算");
  finalView.append(aggregateBtn);

  modal.append(weeklyView, finalView);

  tabWeekly.onclick = () => {
    weeklyView.style.display = "";
    finalView.style.display = "none";
    tabWeekly.style.background = "#111827"; tabWeekly.style.color = "#fff";
    tabFinal.style.background = "#F3F4F6"; tabFinal.style.color = "#111827";
  };
  tabFinal.onclick = () => {
    weeklyView.style.display = "none";
    finalView.style.display = "";
    tabFinal.style.background = "#111827"; tabFinal.style.color = "#fff";
    tabWeekly.style.background = "#F3F4F6"; tabWeekly.style.color = "#111827";
  };

  // Notes
  const notesInput = el("textarea", {
    placeholder: "Notes (選填)",
    style: "width:100%;margin-top:10px;padding:6px;border:1px solid #E5E7EB;border-radius:6px;min-height:60px",
  });
  if (finalRec?.notes) notesInput.value = finalRec.notes;
  modal.append(el("div", { style: "margin-top:12px" },
    el("div", { style: "font-size:12px;color:#374151;margin-bottom:4px" }, "備註"),
    notesInput));

  // Save + Cancel
  const saveBtn = el("button", {
    style: "margin-top:14px;padding:8px 18px;border:0;background:#10B981;color:#fff;border-radius:999px;cursor:pointer;font-weight:600",
    onclick: async () => {
      const records = [];
      // Weekly records — only ones with any non-empty field
      for (let w = 1; w <= weekCount; w++) {
        const perCh = {};
        let touched = false;
        channels.forEach(ch => {
          const ins = weeklyInputs[w][ch];
          const vals = {};
          let any = false;
          Object.keys(ins).forEach(k => {
            const v = ins[k].value;
            if (v !== "") { vals[k] = Number(v); any = true; }
          });
          if (any) { perCh[ch] = vals; touched = true; }
        });
        if (touched) {
          records.push({ scope: "WEEKLY", period_week: w, per_channel: perCh });
        }
      }
      // Final record — only if any field filled
      const finalPerCh = {};
      let finalTouched = false;
      channels.forEach(ch => {
        const ins = finalInputs[ch];
        const vals = {};
        let any = false;
        Object.keys(ins).forEach(k => {
          const v = ins[k].value;
          if (v !== "") { vals[k] = Number(v); any = true; }
        });
        if (any) { finalPerCh[ch] = vals; finalTouched = true; }
      });
      if (finalTouched) {
        records.push({
          scope: "FINAL", period_week: null, per_channel: finalPerCh,
          notes: notesInput.value || null,
        });
      }
      if (!records.length) { alert("沒有任何資料可儲存。"); return; }
      try {
        await apiFetch(`/api/plans/${plan.id}/actuals`, {
          method: "PUT",
          body: JSON.stringify({ records }),
        });
        overlay.remove();
        botSay(`✅ 已儲存 ${records.length} 筆 actuals for ${escapeHTML(plan.name)}.`);
      } catch (e) { showError(e); }
    },
  }, "儲存");
  const cancelBtn = el("button", {
    style: "margin-left:8px;padding:8px 18px;border:1px solid #E5E7EB;background:#fff;border-radius:999px;cursor:pointer",
    onclick: () => overlay.remove(),
  }, "取消");
  modal.append(el("div", { style: "margin-top:10px" }, saveBtn, cancelBtn));

  overlay.append(modal);
  document.body.append(overlay);
}

/* ---------- Reports view ---------- */

async function renderPlanReport(plan) {
  scroll.innerHTML = "";
  const msg = botSay(`📈 成效回顧 · ${escapeHTML(plan.name)}`);
  const card = el("div", { class: "card compare-view full" });
  try {
    const report = await apiFetch(`/api/plans/${plan.id}/report`).then(r => r.json());
    if (report.status === "no_actuals") {
      card.append(
        el("div", { style: "color:#6B7280;padding:12px" },
          "尚未記錄 actuals。點 📊 記錄成效 先把這個 plan 的實際數值存下來。"),
        el("button", {
          style: "padding:6px 14px;border:0;background:#10B981;color:#fff;border-radius:999px;cursor:pointer",
          onclick: () => openActualsModal(plan),
        }, "📊 記錄成效"),
      );
    } else {
      card.append(el("div", { style: "margin-bottom:8px;color:#6B7280" },
        `Source: ${escapeHTML(report.source)}`));
      const tbl = el("table", { class: "tbl" });
      tbl.append(el("thead", {}, el("tr", {},
        el("th", {}, "Channel"),
        el("th", { class: "num" }, "Planned Spend"),
        el("th", { class: "num" }, "Actual Spend"),
        el("th", { class: "num" }, "Variance"),
        el("th", { class: "num" }, "Δ Reach"),
      )));
      const tbody = el("tbody", {});
      report.per_channel.forEach(r => {
        const bg = r.spend_badge === "red" ? "#f8d7da"
                 : r.spend_badge === "amber" ? "#fff3cd" : "#d4edda";
        tbody.append(el("tr", {},
          el("td", {}, r.channel_id),
          el("td", { class: "num" }, Math.round(r.planned_spend_twd).toLocaleString()),
          el("td", { class: "num" }, Math.round(r.actual_spend_twd).toLocaleString()),
          el("td", { class: "num", style: `background:${bg}` },
             (r.spend_variance_pct >= 0 ? "+" : "") + r.spend_variance_pct.toFixed(1) + "%"),
          el("td", { class: "num" },
             (r.net_reach_delta_pp >= 0 ? "+" : "") + r.net_reach_delta_pp.toFixed(1) + "pp"),
        ));
      });
      tbl.append(tbody);
      card.append(tbl);
      const agg = report.aggregate;
      if (agg) {
        card.append(el("div", {
          style: "margin-top:10px;padding:10px;background:#F6F8FA;border-radius:8px",
        },
          el("div", {}, `Total Actual Spend: ${Math.round(agg.actual_spend_twd).toLocaleString()}`),
          el("div", {}, `Spend Variance: ${(agg.spend_variance_pct >= 0 ? "+" : "") + agg.spend_variance_pct.toFixed(1)}%`),
          el("div", {}, `Net Reach Δ: ${(agg.net_reach_delta_pp >= 0 ? "+" : "") + agg.net_reach_delta_pp.toFixed(1)}pp`),
        ));
      }
      card.append(el("div", { style: "margin-top:10px" },
        el("a", {
          href: `/api/plans/${plan.id}/report.html`, target: "_blank",
          style: "padding:6px 14px;border:1px solid #111827;color:#111827;background:#fff;border-radius:999px;text-decoration:none;display:inline-block",
        }, "🖨 開啟列印用 report.html")));
    }
  } catch (e) { showError(e); }
  msg.append(card);
}

/* ---------- renderChannels injection: call banner helper ---------- */
const _origRenderChannels = renderChannels;
renderChannels = function(msg) {
  _origRenderChannels(msg);
  const card = msg.querySelector(".card");
  if (card) maybeShowRecommendFillBanner(card);
};

/* ---------- renderProjectDetail injection: per-plan actuals + reports buttons ---------- */
const _origRenderProjectDetail = renderProjectDetail;
renderProjectDetail = async function(projectId) {
  await _origRenderProjectDetail(projectId);
  // Inject action buttons next to every plan row's budget column.
  const planRows = scroll.querySelectorAll("table.tbl tbody tr");
  const plans = await api.projectPlans(projectId);
  planRows.forEach((tr, idx) => {
    // We target plan tables by their header match; skip session tables.
    const firstHeader = tr.parentElement?.previousElementSibling?.querySelector("th")?.textContent || "";
    if (firstHeader !== "Plan") return;
    const plan = plans[idx];
    if (!plan) return;
    const actionsTd = el("td", { style: "white-space:nowrap" },
      el("button", {
        style: "padding:3px 8px;margin-right:4px;border-radius:999px;border:1px solid #10B981;background:#fff;color:#10B981;cursor:pointer;font-size:12px",
        onclick: () => openActualsModal(plan),
      }, "📊 記錄成效"),
      el("button", {
        style: "padding:3px 8px;border-radius:999px;border:1px solid #111827;background:#fff;color:#111827;cursor:pointer;font-size:12px",
        onclick: () => renderPlanReport(plan),
      }, "📈 成效回顧"),
    );
    tr.append(actionsTd);
  });
};

/* ========================================================================
 * v6 · PR B — Calibration Settings panel + confidence badges
 * ====================================================================== */

/* Text copy used by the UI. Keeps the Chinese labels in one place so
 * they're easy to review / re-word. */
const CONFIDENCE_COPY = {
  high: "高信心 · 已累積資料",
  mid:  "中等信心 · 建議再跑一檔",
  low:  "資料不足 · 建議先用 system default",
};

function _bucketForScore(score, thresholds) {
  const hi = thresholds?.high ?? 70;
  const mid = thresholds?.mid ?? 40;
  return score >= hi ? "high" : score >= mid ? "mid" : "low";
}

function _confidenceBadge(score, bucket) {
  const colour = bucket === "high" ? "#10B981"
               : bucket === "mid"  ? "#F59E0B"
               : "#EF4444";
  const cls = `confidence-${bucket}`;
  const tip = `Score ${score} / 100 (依樣本數 + 一致性計算)`;
  return el("span", {
    class: cls, title: tip,
    style: `display:inline-block;padding:2px 8px;border-radius:999px;` +
           `background:${colour};color:#fff;font-size:11px;font-weight:600`,
  }, `${score} · ${CONFIDENCE_COPY[bucket] || bucket}`);
}

async function openCalibrationSettings() {
  const overlay = el("div", {
    style: "position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:1000;" +
           "display:flex;align-items:center;justify-content:center",
  });
  const modal = el("div", {
    style: "background:#fff;border-radius:12px;padding:20px;width:min(880px,92vw);" +
           "max-height:88vh;overflow:auto",
  });
  modal.append(
    el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:10px" },
      el("h3", { style: "margin:0" }, "⚙️ 校正設定"),
      el("button", {
        style: "border:0;background:#F3F4F6;padding:4px 10px;border-radius:999px;cursor:pointer",
        onclick: () => overlay.remove(),
      }, "✕")),
    el("p", { style: "margin:0 0 10px;color:#6B7280;font-size:13px" },
       "調整 CalibrationProfile 的半衰期與信心門檻。")
  );
  const body = el("div", {});
  modal.append(body);

  async function refresh() {
    body.innerHTML = "";
    try {
      const settings = await apiFetch("/api/calibration/settings").then(r => r.json());
      const profiles = await apiFetch("/api/calibration/profiles").then(r => r.json());
      // v6 gap-audit · Issue 19 — global scope is admin-only (PRD §FR-34).
      // Non-admins see the value as read-only with an explanatory note.
      const me = await api.me().catch(() => null);
      const isAdmin = !!(me && me.is_admin);

      // Global half-life slider
      const hlCurrent = settings?.global?.half_life_days ?? 180;
      const hlLabel = el("div", {},
        el("span", { style: "font-weight:600" }, "近期權重（半衰期）"),
        el("span", { style: "color:#6B7280;margin-left:8px;font-size:12px" },
          `目前 ${hlCurrent} 天 — 數字越小，越早的資料影響越少。`));
      const hlSlider = el("input", {
        type: "range", min: "30", max: "365", step: "30",
        value: String(hlCurrent),
        ...(isAdmin ? {} : { disabled: "" }),
        style: "width:100%",
      });
      const hlNumber = el("input", {
        type: "number", value: String(hlCurrent), step: "1", min: "1",
        ...(isAdmin ? {} : { disabled: "", readonly: "" }),
        style: "width:90px;margin-left:8px",
      });
      hlSlider.oninput = () => { hlNumber.value = hlSlider.value; };
      hlNumber.oninput = () => { hlSlider.value = hlNumber.value; };

      // Build the action row conditionally — admins get apply/reset;
      // non-admins get a short "由 admin 管理" note instead.
      const actionRow = el("div",
        { style: "display:flex;align-items:center;gap:4px;margin-top:6px" },
        hlSlider, hlNumber);
      if (isAdmin) {
        const hlApply = el("button", {
          style: "padding:6px 14px;border:0;background:#8B5CF6;color:#fff;border-radius:999px;cursor:pointer;margin-left:8px",
          onclick: async () => {
            const days = Number(hlNumber.value);
            if (!(days > 0)) return;
            try {
              await apiFetch("/api/calibration/settings", {
                method: "PUT",
                body: JSON.stringify({ scope: "global", half_life_days: days }),
              });
              botSay(`已將 half_life_days 設為 ${days} 天。`);
            } catch (e) { showError(e); }
            refresh();
          },
        }, "套用");
        const hlReset = el("button", {
          style: "padding:6px 14px;border:1px solid #E5E7EB;background:#fff;border-radius:999px;cursor:pointer;margin-left:6px",
          onclick: async () => {
            try {
              await apiFetch("/api/calibration/settings?scope=global", { method: "DELETE" });
              botSay("已還原 global half_life_days = 180 天。");
            } catch (e) { showError(e); }
            refresh();
          },
        }, "還原預設");
        actionRow.append(hlApply, hlReset);
      } else {
        actionRow.append(el("span", {
          style: "margin-left:8px;font-size:12px;color:#6B7280;font-style:italic",
        }, "由 admin 管理 · 唯讀（可至 per-client / per-channel 調整你自己的 override）"));
      }
      body.append(
        el("div", { style: "margin-bottom:18px;padding:12px;border:1px solid #E5E7EB;border-radius:8px" },
          hlLabel, actionRow),
      );

      // Profiles table
      body.append(el("h5", { style: "margin:8px 0" }, `Calibration Profiles (${profiles.length})`));
      if (profiles.length === 0) {
        body.append(el("div", { style: "color:#6B7280" },
          "尚未累積任何資料。完成 plan 後記錄 actuals，系統會自動建立 profile。"));
      } else {
        const tbl = el("table", { class: "tbl" });
        tbl.append(el("thead", {}, el("tr", {},
          el("th", {}, "Client"), el("th", {}, "Target"),
          el("th", {}, "Channel"), el("th", {}, "Metric"),
          el("th", { class: "num" }, "Mean"),
          el("th", { class: "num" }, "n_raw"),
          el("th", { class: "num" }, "n_eff"),
          el("th", {}, "Confidence"),
          el("th", {}, ""),
        )));
        const tbody = el("tbody", {});
        profiles.forEach(p => {
          const bucket = _bucketForScore(p.confidence_score, settings?.global?.thresholds);
          const cls = `confidence-${bucket}`;
          tbody.append(el("tr", { class: cls },
            el("td", {}, p.client_id),
            el("td", {}, p.target_id),
            el("td", {}, p.channel_id),
            el("td", {}, p.metric),
            el("td", { class: "num" }, (p.value_mean_weighted || 0).toFixed(2)),
            el("td", { class: "num" }, String(p.n_raw)),
            el("td", { class: "num" }, (p.n_effective || 0).toFixed(2)),
            el("td", {}, _confidenceBadge(p.confidence_score, bucket)),
            el("td", {},
              el("button", {
                style: "padding:3px 8px;border:1px solid #111827;background:#fff;border-radius:999px;cursor:pointer;font-size:11px",
                onclick: () => openObservationDrawer(p),
              }, "觀察值")),
          ));
        });
        tbl.append(tbody);
        body.append(tbl);
      }
    } catch (e) { showError(e); }
  }

  async function openObservationDrawer(profile) {
    const rows = await apiFetch(
      `/api/calibration/observations?client_id=${encodeURIComponent(profile.client_id)}` +
      `&target_id=${encodeURIComponent(profile.target_id)}` +
      `&channel_id=${encodeURIComponent(profile.channel_id)}` +
      `&metric=${encodeURIComponent(profile.metric)}`
    ).then(r => r.json());
    const drawer = el("div", {
      style: "position:fixed;right:0;top:0;bottom:0;width:min(520px,88vw);background:#fff;" +
             "border-left:1px solid #E5E7EB;padding:16px;overflow:auto;z-index:1100",
    });
    drawer.append(
      el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:10px" },
        el("h4", { style: "margin:0" },
          `Observations · ${profile.channel_id} / ${profile.metric}`),
        el("button", {
          style: "border:0;background:#F3F4F6;padding:4px 10px;border-radius:999px;cursor:pointer",
          onclick: () => drawer.remove(),
        }, "✕")),
      el("p", { style: "color:#6B7280;font-size:12px;margin:0 0 10px" },
        "把 weight_override 設為 0 可排除異常值、1 可強制全權計入。留空則依半衰期計算。"),
    );
    rows.forEach(r => {
      const weightInput = el("input", {
        type: "number", step: "0.01", min: "0", max: "1",
        value: r.weight_override == null ? "" : String(r.weight_override),
        style: "width:70px;padding:3px 6px;border:1px solid #E5E7EB;border-radius:6px",
      });
      const savebtn = el("button", {
        style: "margin-left:6px;padding:3px 10px;border:0;background:#10B981;color:#fff;border-radius:999px;cursor:pointer;font-size:12px",
        onclick: async () => {
          const val = weightInput.value === "" ? null : Number(weightInput.value);
          await apiFetch(`/api/calibration/observations/${r.id}`, {
            method: "PATCH",
            body: JSON.stringify({ weight_override: val }),
          });
          botSay(`Observation ${r.id} weight_override = ${val == null ? "(clear)" : val}.`);
          drawer.remove();
          refresh();
        },
      }, "存");
      drawer.append(
        el("div", { style: "border-top:1px solid #F3F4F6;padding:8px 0;display:grid;grid-template-columns:1fr auto;gap:6px;align-items:center" },
          el("div", {},
            el("div", { style: "font-size:12px;color:#111827" },
              `value = ${r.value.toFixed(2)}`),
            el("div", { style: "font-size:11px;color:#6B7280" },
              `observed_at = ${new Date(r.observed_at * 1000).toISOString().slice(0, 10)}`),
          ),
          el("div", { style: "display:flex;align-items:center" },
            weightInput, savebtn),
        )
      );
    });
    document.body.append(drawer);
  }

  await refresh();
  overlay.append(modal);
  document.body.append(overlay);
}

/* Expose a Home-screen button to open the Calibration Settings panel.
 * Drops a button into the topbar via delegation on first render. */
function _installCalibrationTopbarButton() {
  if (document.getElementById("btnCalibration")) return;
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  const btn = el("button", {
    id: "btnCalibration", class: "reset",
    title: "Calibration Settings",
    onclick: () => openCalibrationSettings(),
  }, "⚙️");
  topbar.insertBefore(btn, document.getElementById("btnLogout"));
}
_installCalibrationTopbarButton();

/* Enrich the Reports view with confidence badges drawn from the profile
 * for this plan's (client × target × channel). Wraps the PR A function
 * that already walks per-channel rows. */
const _origRenderPlanReport = renderPlanReport;
renderPlanReport = async function(plan) {
  await _origRenderPlanReport(plan);
  // After the base renderer, look up profiles + patch in confidence badges.
  try {
    const sess = await apiFetch(`/api/sessions/${encodeURIComponent(plan.brief_id)}`)
      .then(r => r.json())
      .catch(() => null);
    if (!sess) return;
    const clientId = sess.session?.brief?.client_id;
    const targetId = (sess.session?.brief?.target_ids || [])[0];
    if (!clientId || !targetId) return;
    const settings = await apiFetch("/api/calibration/settings").then(r => r.json());
    const profiles = await apiFetch("/api/calibration/profiles").then(r => r.json());
    const byCh = {};
    profiles.forEach(p => {
      if (p.client_id === clientId && p.target_id === targetId
          && p.metric === "cpm_twd") {
        byCh[p.channel_id] = p;
      }
    });
    scroll.querySelectorAll("table.tbl tbody tr").forEach(tr => {
      const ch = tr.children[0]?.textContent;
      const p = byCh[ch];
      if (!p) return;
      const bucket = _bucketForScore(p.confidence_score, settings?.global?.thresholds);
      const badgeTd = el("td", {}, _confidenceBadge(p.confidence_score, bucket));
      tr.append(badgeTd);
    });
  } catch (_) { /* best-effort */ }
};

/* Install #navHome click (delegated) */
document.addEventListener("click", (e) => {
  const t = e.target;
  if (!(t instanceof HTMLElement)) return;
  if (t.id === "btnHome") { renderProjects(); }
  if (t.id === "btnHistory") { renderHistory(); }
  if (t.id === "btnUsers") { renderUsers(); }
  if (t.id === "btnLogout") { setApiKey(""); bootApp(); }
});

/* ========================================================================
 * v6 · PR C — Frontend UX completion (overrides modal, CAL pill, chart,
 *             scope overrides, rich tooltip, history viewer, quality)
 * ====================================================================== */

/* ---------- Issue 16: render-epoch staleness guard ---------- */
state._renderEpoch = 0;
function _bumpEpoch() { state._renderEpoch = (state._renderEpoch | 0) + 1; return state._renderEpoch; }

/* Wrap top-level render entry points so every async continuation can
 * compare its captured epoch against state._renderEpoch and bail when
 * the user has navigated away. We only retrofit checks into NEW async
 * continuations below; existing renderers keep working unchanged. */
(function _wrapTopLevelRendersForEpoch() {
  const wrap = (name) => {
    const fn = window[name];
    if (typeof fn !== "function") return;
    window[name] = function (...args) {
      _bumpEpoch();
      return fn.apply(this, args);
    };
  };
  wrap("renderProjects");
  wrap("renderProjectDetail");
  wrap("renderPlanReport");
  wrap("renderCompare");
  wrap("renderHistory");
  wrap("renderStep");
})();

/* ---------- Issue 2: Channel overrides modal ---------- */

// Per-row editable field list (CPM, Pen%, Reach%, BuyingAud, Impressions).
const _OVERRIDE_FIELDS = [
  ["cpm_twd",             "CPM (TWD)",          1,    "CPM"],
  ["penetration_pct",     "Penetration %",      0.1,  "Pen%"],
  ["net_reach_pct",       "Net Reach %",        0.1,  "Reach%"],
  ["buying_audience_000", "Buying Audience (千人)", 1, "BA"],
  ["impressions",         "Impressions",        1000, "Impr"],
];

/** Open the overrides editor for the current session. Returns a Promise
 *  that resolves once the modal is dismissed (saved OR cancelled) so
 *  callers (e.g. the recommend-fill banner) can chain on close.
 *
 *  @param {object} session  Must carry brief.channel_ids, brief.client_id,
 *                            brief.target_ids, and optional brief.overrides.
 */
async function openOverridesModal(session) {
  return new Promise(async (resolve) => {
    const brief = session?.brief || {};
    const channels = brief.channel_ids || [];
    const clientId = brief.client_id;
    const targetId = (brief.target_ids || [])[0];

    // Defaults — prefer calibrated values if we have profiles, otherwise
    // fall back to the static reference CCS CPM / penetration.
    let calSummary = {};
    let refMetrics = {};
    try {
      if (clientId && targetId) {
        calSummary = await apiFetch(
          `/api/calibration/channel-summary?client_id=${encodeURIComponent(clientId)}` +
          `&target_id=${encodeURIComponent(targetId)}`
        ).then(r => r.ok ? r.json() : {});
      }
    } catch (_) { /* best-effort */ }
    try {
      const refResp = await apiFetch("/api/reference/channels").then(r => r.json());
      refMetrics = refResp?.metrics || {};
    } catch (_) { /* best-effort */ }

    // Also fetch calibrated profiles so we can surface CAL: <num> defaults.
    let calProfiles = {};
    try {
      const rows = await apiFetch("/api/calibration/profiles").then(r => r.json());
      (rows || []).forEach(p => {
        if (clientId && targetId
            && p.client_id === clientId && p.target_id === targetId) {
          calProfiles[`${p.channel_id}__${p.metric}`] = p;
        }
      });
    } catch (_) { /* best-effort */ }

    const overlay = el("div", {
      style: "position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:1000;" +
             "display:flex;align-items:center;justify-content:center",
    });
    const modal = el("div", {
      style: "background:#fff;border-radius:12px;padding:20px;width:min(1040px,94vw);" +
             "max-height:90vh;overflow:auto",
    });

    const close = (_reason) => { overlay.remove(); resolve(); };
    modal.append(
      el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:8px" },
        el("h3", { style: "margin:0" }, "✏️ 調整預設值 (CPM / Penetration / Reach / 人數 / Impressions)"),
        el("button", {
          style: "border:0;background:#F3F4F6;padding:4px 10px;border-radius:999px;cursor:pointer",
          onclick: () => close("close-x"),
        }, "✕")),
      el("p", { style: "margin:0 0 10px;color:#6B7280;font-size:13px" },
        "留空=沿用系統預設；填入=以此覆寫。粗體代表目前有 override。"),
    );

    const tbl = el("table", { class: "tbl", style: "width:100%" });
    const head = el("tr", {}, el("th", {}, "Channel"));
    _OVERRIDE_FIELDS.forEach(([, label]) => head.append(el("th", { class: "num" }, label)));
    head.append(el("th", {}, ""));
    tbl.append(el("thead", {}, head));
    const tbody = el("tbody", {});

    const rowInputs = {};  // channel -> { field -> <input> }
    const defaultCells = {}; // channel -> { field -> <span> }

    channels.forEach(ch => {
      const tr = el("tr", { "data-override-row": ch });
      tr.append(el("td", {}, ch));
      rowInputs[ch] = {};
      defaultCells[ch] = {};
      const existingOv = (brief.overrides || {})[ch] || {};

      _OVERRIDE_FIELDS.forEach(([field, , step]) => {
        const hasCal = calSummary[ch]?.has_profile;
        const calProfile = calProfiles[`${ch}__${field}`];
        let defaultText = "—";
        if (calProfile && calProfile.value_mean_weighted != null) {
          defaultText = `CAL: ${Number(calProfile.value_mean_weighted).toFixed(2)}`;
        } else {
          // Static reference fallback.
          const m = refMetrics[ch] || {};
          if (field === "cpm_twd" && m.cpm_twd != null)
            defaultText = `${Number(m.cpm_twd).toFixed(2)}`;
          else if (field === "penetration_pct" && m.penetration_pct != null)
            defaultText = `${Number(m.penetration_pct).toFixed(2)}%`;
          else if (field === "net_reach_pct" && m.penetration_pct != null)
            defaultText = "—";
        }
        const defSpan = el("div", {
          style: "font-size:10px;color:#9CA3AF;margin-bottom:2px",
        }, defaultText);
        defaultCells[ch][field] = defSpan;

        const current = existingOv[field];
        const input = el("input", {
          type: "number", step: String(step), min: "0",
          value: current == null ? "" : String(current),
          style: "width:100%;padding:4px 6px;border:1px solid #E5E7EB;border-radius:6px;font-size:12px"
            + (current != null ? ";font-weight:700" : ""),
        });
        input.addEventListener("input", () => {
          input.style.fontWeight = input.value === "" ? "normal" : "700";
        });
        rowInputs[ch][field] = input;

        tr.append(el("td", { class: "num" }, defSpan, input));
      });

      // Clear row button.
      const clearBtn = el("button", {
        style: "padding:3px 8px;border:1px solid #E5E7EB;background:#fff;border-radius:999px;cursor:pointer;font-size:11px",
        onclick: () => {
          Object.values(rowInputs[ch]).forEach(inp => {
            inp.value = "";
            inp.style.fontWeight = "normal";
          });
        },
      }, "清除");
      tr.append(el("td", {}, clearBtn));
      tbody.append(tr);
    });
    tbl.append(tbody);
    modal.append(tbl);

    // Save / cancel footer
    const saveBtn = el("button", {
      style: "padding:8px 18px;border:0;background:#10B981;color:#fff;border-radius:999px;cursor:pointer;font-weight:600",
      onclick: async () => {
        const overrides = {};
        channels.forEach(ch => {
          const row = {};
          let any = false;
          _OVERRIDE_FIELDS.forEach(([field]) => {
            const v = rowInputs[ch][field].value;
            if (v !== "") { row[field] = Number(v); any = true; }
          });
          if (any) overrides[ch] = row;
        });
        try {
          await api.advance(state.sessionId, { overrides });
          // Sticky override payloads don't change the step; refresh brief locally.
          if (state.session?.brief) state.session.brief.overrides = overrides;
          botSay(`✅ 已儲存 ${Object.keys(overrides).length} 個 channel 的 override。`);
        } catch (e) { showError(e); }
        close("save");
      },
    }, "儲存");
    const cancelBtn = el("button", {
      style: "margin-left:8px;padding:8px 18px;border:1px solid #E5E7EB;background:#fff;border-radius:999px;cursor:pointer",
      onclick: () => close("cancel"),
    }, "取消");
    modal.append(el("div", { style: "margin-top:14px;display:flex;justify-content:flex-end" }, cancelBtn, saveBtn));

    overlay.append(modal);
    document.body.append(overlay);
  });
}

/* ---------- Issue 3: CAL pill on Channel step ---------- */

const _CAL_PILL_BG = { high: "#10B981", mid: "#F59E0B", low: "#EF4444" };

async function _attachCalPills(card, brief) {
  if (!brief?.client_id || !(brief.target_ids || []).length) return;
  const targetId = brief.target_ids[0];
  const epoch = state._renderEpoch;
  let summary = {};
  try {
    const r = await apiFetch(
      `/api/calibration/channel-summary?client_id=${encodeURIComponent(brief.client_id)}` +
      `&target_id=${encodeURIComponent(targetId)}`
    );
    if (!r.ok) return;
    summary = await r.json();
  } catch (_) { return; }
  if (epoch !== state._renderEpoch) return;
  let settings = {};
  try {
    settings = await apiFetch("/api/calibration/settings").then(r => r.json());
  } catch (_) { settings = {}; }
  if (epoch !== state._renderEpoch) return;

  card.querySelectorAll("input[type=checkbox]").forEach(cb => {
    const info = summary[cb.value];
    if (!info || !info.has_profile) return;
    const score = info.confidence_score != null ? info.confidence_score : 0;
    const bucket = info.bucket || _bucketForScore(score, settings?.global?.thresholds);
    const pill = el("span", {
      class: `cal-pill confidence-${bucket}`,
      title: `Calibrated · score ${score}`,
      style: `display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;`
           + `background:${_CAL_PILL_BG[bucket] || "#9CA3AF"};color:#fff;font-size:10px;font-weight:700`,
    }, `CAL · ${score}`);
    // Insert after label text (the parent label contains cb + text).
    cb.parentNode?.append(pill);
  });
}

/* ---------- Issue 2b: wire overrides button into renderChannels + renderReview ---------- */

const _origRenderChannelsPRC = renderChannels;
renderChannels = function(msg) {
  _origRenderChannelsPRC(msg);
  const card = msg.querySelector(".card");
  if (!card) return;
  // Attach CAL pills for each calibrated channel (Issue 3).
  _attachCalPills(card, state.session?.brief);
  // Inject the "✏️ 調整預設值" button inside the card header area.
  const btn = el("button", {
    style: "margin-left:8px;padding:4px 10px;border:1px solid #8B5CF6;background:#fff;"
         + "color:#8B5CF6;border-radius:999px;cursor:pointer;font-size:12px",
    onclick: () => openOverridesModal(state.session),
  }, "✏️ 調整預設值");
  const header = card.querySelector("h5");
  if (header) header.append(btn);
};

const _origRenderReviewPRC = renderReview;
renderReview = function(msg) {
  _origRenderReviewPRC(msg);
  const card = msg.querySelector(".card");
  if (!card) return;
  const btn = el("button", {
    style: "margin-top:8px;padding:6px 12px;border:1px solid #8B5CF6;background:#fff;"
         + "color:#8B5CF6;border-radius:999px;cursor:pointer;font-size:12px",
    onclick: () => openOverridesModal(state.session),
  }, "✏️ 調整 CPM/Penetration");
  card.append(btn);
};

/* ---------- Issue 6: replace the Fill-Now banner CTA to call overrides modal ---------- */

const _origMaybeShowBanner = maybeShowRecommendFillBanner;
maybeShowRecommendFillBanner = async function(card) {
  const b = state.session?.brief;
  if (!b?.client_id || !(b.target_ids || []).length) return;
  if (state.bannerDismissed) return;
  const target = b.target_ids[0];
  try {
    const r = await apiFetch(
      `/api/calibration/coverage?client_id=${encodeURIComponent(b.client_id)}` +
      `&target_id=${encodeURIComponent(target)}`
    );
    const body = await r.json();
    if (body.has_history) return;
  } catch (_) { return; }

  const banner = el("div", {
    class: "card recommend-fill-banner",
    style: "background:#FFFBEB;border:1px solid #F59E0B;margin:8px 0;padding:10px 12px",
  });
  banner.append(
    el("div", { style: "font-weight:700;color:#92400E;margin-bottom:4px" },
       RECOMMEND_FILL_BANNER.headline),
    el("div", { style: "font-size:13px;color:#7C2D12;margin-bottom:6px" },
       RECOMMEND_FILL_BANNER.body),
    el("div", { style: "display:flex;gap:8px" },
      el("button", {
        style: "padding:6px 12px;border-radius:999px;border:0;background:#F59E0B;color:#fff;cursor:pointer",
        onclick: async () => {
          // Issue 6: jump straight to overrides modal; banner goes away.
          await openOverridesModal(state.session);
          banner.remove();
          state.bannerDismissed = true;
        },
      }, RECOMMEND_FILL_BANNER.cta),
      el("button", {
        style: "padding:6px 12px;border-radius:999px;border:1px solid #E5E7EB;background:#fff;cursor:pointer",
        onclick: () => { state.bannerDismissed = true; banner.remove(); },
      }, "本次先跳過"),
    ),
  );
  card.parentNode?.insertBefore(banner, card);
};

/* ---------- Issue 4: Chart.js bar chart in renderPlanReport ---------- */

async function _injectReportChart(plan) {
  const epoch = state._renderEpoch;
  // Fetch report fresh rather than trying to parse DOM tables.
  let report;
  try {
    report = await apiFetch(`/api/plans/${plan.id}/report`).then(r => r.json());
  } catch (_) { return; }
  if (epoch !== state._renderEpoch) return;
  if (!report || report.status === "no_actuals" || !report.per_channel) return;

  // Find the rendered report card in scroll.
  const card = scroll.querySelector(".compare-view.full");
  if (!card) return;

  // Insert the chart canvas above the existing table (fallback remains visible).
  const canvas = el("canvas", {
    id: "chart-report-spend",
    style: "max-height:280px;margin-bottom:10px",
  });
  const chartWrap = el("div", { class: "card report-chart" },
    el("h5", { style: "margin:0 0 6px" }, "Planned vs Actual Spend"),
    canvas);
  // Insert before the first table.
  const firstTable = card.querySelector("table.tbl");
  if (firstTable) card.insertBefore(chartWrap, firstTable);
  else card.prepend(chartWrap);

  // Also extend the aggregate panel with CPM + Impressions variance.
  const agg = report.aggregate || {};
  // Weighted rollups (by planned spend) so numbers stay sane when the
  // backend doesn't already include them.
  let cpmVarAgg = agg.cpm_variance_pct;
  let imprVarAgg = agg.impressions_variance_pct;
  if (cpmVarAgg == null || imprVarAgg == null) {
    let wSum = 0, cpmAcc = 0, imprAcc = 0;
    (report.per_channel || []).forEach(r => {
      const w = Number(r.planned_spend_twd || 0);
      if (!(w > 0)) return;
      wSum += w;
      if (r.cpm_variance_pct != null) cpmAcc += w * Number(r.cpm_variance_pct);
      if (r.impressions_variance_pct != null)
        imprAcc += w * Number(r.impressions_variance_pct);
    });
    if (wSum > 0) {
      if (cpmVarAgg == null) cpmVarAgg = cpmAcc / wSum;
      if (imprVarAgg == null) imprVarAgg = imprAcc / wSum;
    }
  }

  // Append extra variance rows to the summary panel (the greyish box).
  const summary = [...card.querySelectorAll("div")].reverse().find(d =>
    d.style && d.style.background && d.style.background.indexOf("F6F8FA") !== -1);
  if (summary) {
    if (cpmVarAgg != null && !summary.dataset.cpmInjected) {
      summary.dataset.cpmInjected = "1";
      summary.append(el("div", {},
        `CPM Variance: ${(cpmVarAgg >= 0 ? "+" : "") + Number(cpmVarAgg).toFixed(1)}%`));
    }
    if (imprVarAgg != null && !summary.dataset.imprInjected) {
      summary.dataset.imprInjected = "1";
      summary.append(el("div", {},
        `Impressions Variance: ${(imprVarAgg >= 0 ? "+" : "") + Number(imprVarAgg).toFixed(1)}%`));
    }
  }

  // Load Chart.js (lazy); fall back silently if offline.
  try {
    const Chart = await loadChartLib();
    if (!Chart) return;
    if (epoch !== state._renderEpoch) return;
    const labels = report.per_channel.map(r => r.channel_id);
    const planned = report.per_channel.map(r => Math.round(r.planned_spend_twd || 0));
    const actual = report.per_channel.map(r => Math.round(r.actual_spend_twd || 0));
    new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Planned Spend", backgroundColor: PALETTE[0], data: planned },
          { label: "Actual Spend",  backgroundColor: PALETTE[2], data: actual  },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { position: "bottom" }, title: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  } catch (err) {
    console.warn("Report chart failed — table fallback remains", err);
  }
}

const _origRenderPlanReportPRC = renderPlanReport;
renderPlanReport = async function(plan) {
  const epoch = _bumpEpoch();
  await _origRenderPlanReportPRC(plan);
  if (epoch !== state._renderEpoch) return;
  // Inject chart + variance stats (Issue 4). Guarded by epoch so the
  // async continuation can't touch DOM after navigation.
  _injectReportChart(plan);
};

/* ---------- Issue 5: per-client + per-channel half-life override forms ---------- */

async function _renderScopeOverrideForms(body, settings, profiles) {
  // Derive unique clients / (client,target) / (client,target,channel) from profiles.
  const clients = [...new Set((profiles || []).map(p => p.client_id))].sort();
  const perClient = (settings.per_client || []);
  const perChannel = (settings.per_channel || []);

  // --- Per-client form ---
  const pcTitle = el("h5", { style: "margin:16px 0 6px" }, "per-client half-life override");
  body.append(pcTitle);
  const pcForm = el("div", { style: "display:flex;gap:6px;align-items:center;margin-bottom:6px" });
  const pcClient = el("select", { style: "padding:4px 8px;border:1px solid #E5E7EB;border-radius:6px" },
    el("option", { value: "" }, "選擇 client…"),
    ...clients.map(c => el("option", { value: c }, c))
  );
  const pcDays = el("input", { type: "number", min: "1", step: "1", placeholder: "days", style: "width:90px;padding:4px 8px;border:1px solid #E5E7EB;border-radius:6px" });
  const pcAdd = el("button", {
    style: "padding:4px 12px;border:0;background:#8B5CF6;color:#fff;border-radius:999px;cursor:pointer;font-size:12px",
    onclick: async () => {
      if (!pcClient.value || !Number(pcDays.value)) return;
      await apiFetch("/api/calibration/settings", {
        method: "PUT",
        body: JSON.stringify({ scope: "client", client_id: pcClient.value, half_life_days: Number(pcDays.value) }),
      });
      botSay(`已新增 per-client override · ${pcClient.value} = ${pcDays.value} 天.`);
      _reopenCalibrationSettings();
    },
  }, "新增 override");
  pcForm.append(pcClient, pcDays, pcAdd);
  body.append(pcForm);
  // Existing per-client overrides table.
  if (perClient.length) {
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Client"),
      el("th", { class: "num" }, "half-life (days)"),
      el("th", {}, ""),
    )));
    const tb = el("tbody", {});
    perClient.forEach(r => {
      tb.append(el("tr", {},
        el("td", {}, r.client_id),
        el("td", { class: "num" }, String(r.half_life_days)),
        el("td", {}, el("button", {
          style: "padding:3px 10px;border:1px solid #E5E7EB;background:#fff;border-radius:999px;cursor:pointer;font-size:11px",
          onclick: async () => {
            await apiFetch(`/api/calibration/settings?scope=client&client_id=${encodeURIComponent(r.client_id)}`, { method: "DELETE" });
            botSay(`已還原 per-client override (${r.client_id}).`);
            _reopenCalibrationSettings();
          },
        }, "還原")),
      ));
    });
    tbl.append(tb);
    body.append(tbl);
  }

  // --- Per-channel form ---
  body.append(el("h5", { style: "margin:16px 0 6px" }, "per-channel half-life override"));
  // Build dropdowns from profiles.
  const byClient = {};
  (profiles || []).forEach(p => {
    const c = byClient[p.client_id] || (byClient[p.client_id] = {});
    const t = c[p.target_id] || (c[p.target_id] = new Set());
    t.add(p.channel_id);
  });
  const pchForm = el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px" });
  const pchClient = el("select", { style: "padding:4px 8px;border:1px solid #E5E7EB;border-radius:6px" },
    el("option", { value: "" }, "client…"),
    ...Object.keys(byClient).sort().map(c => el("option", { value: c }, c))
  );
  const pchTarget = el("select", { style: "padding:4px 8px;border:1px solid #E5E7EB;border-radius:6px" },
    el("option", { value: "" }, "target…")
  );
  const pchChannel = el("select", { style: "padding:4px 8px;border:1px solid #E5E7EB;border-radius:6px" },
    el("option", { value: "" }, "channel…")
  );
  const pchDays = el("input", { type: "number", min: "1", step: "1", placeholder: "days", style: "width:90px;padding:4px 8px;border:1px solid #E5E7EB;border-radius:6px" });
  pchClient.addEventListener("change", () => {
    pchTarget.innerHTML = "";
    pchTarget.append(el("option", { value: "" }, "target…"));
    const tmap = byClient[pchClient.value] || {};
    Object.keys(tmap).sort().forEach(t =>
      pchTarget.append(el("option", { value: t }, t)));
    pchChannel.innerHTML = "";
    pchChannel.append(el("option", { value: "" }, "channel…"));
  });
  pchTarget.addEventListener("change", () => {
    pchChannel.innerHTML = "";
    pchChannel.append(el("option", { value: "" }, "channel…"));
    const chs = (byClient[pchClient.value] || {})[pchTarget.value];
    if (chs) [...chs].sort().forEach(c => pchChannel.append(el("option", { value: c }, c)));
  });
  const pchAdd = el("button", {
    style: "padding:4px 12px;border:0;background:#8B5CF6;color:#fff;border-radius:999px;cursor:pointer;font-size:12px",
    onclick: async () => {
      if (!pchClient.value || !pchTarget.value || !pchChannel.value || !Number(pchDays.value)) return;
      await apiFetch("/api/calibration/settings", {
        method: "PUT",
        body: JSON.stringify({
          scope: "channel", client_id: pchClient.value,
          target_id: pchTarget.value, channel_id: pchChannel.value,
          half_life_days: Number(pchDays.value),
        }),
      });
      botSay(`已新增 per-channel override · ${pchChannel.value} = ${pchDays.value} 天.`);
      _reopenCalibrationSettings();
    },
  }, "新增 override");
  pchForm.append(pchClient, pchTarget, pchChannel, pchDays, pchAdd);
  body.append(pchForm);
  if (perChannel.length) {
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Client"), el("th", {}, "Target"), el("th", {}, "Channel"),
      el("th", { class: "num" }, "half-life (days)"), el("th", {}, ""),
    )));
    const tb = el("tbody", {});
    perChannel.forEach(r => {
      tb.append(el("tr", {},
        el("td", {}, r.client_id),
        el("td", {}, r.target_id),
        el("td", {}, r.channel_id),
        el("td", { class: "num" }, String(r.half_life_days)),
        el("td", {}, el("button", {
          style: "padding:3px 10px;border:1px solid #E5E7EB;background:#fff;border-radius:999px;cursor:pointer;font-size:11px",
          onclick: async () => {
            const url = `/api/calibration/settings?scope=channel`
                      + `&client_id=${encodeURIComponent(r.client_id)}`
                      + `&target_id=${encodeURIComponent(r.target_id)}`
                      + `&channel_id=${encodeURIComponent(r.channel_id)}`;
            await apiFetch(url, { method: "DELETE" });
            botSay("已還原 per-channel override.");
            _reopenCalibrationSettings();
          },
        }, "還原")),
      ));
    });
    tbl.append(tb);
    body.append(tbl);
  }
}

function _reopenCalibrationSettings() {
  // Close any open overlay + re-open.
  document.querySelectorAll("[data-cal-settings-overlay]").forEach(x => x.remove());
  openCalibrationSettings();
}

/* ---------- Issue 5 + 7 + 8 — wrap openCalibrationSettings ---------- */

const _origOpenCalibrationSettings = openCalibrationSettings;
openCalibrationSettings = async function() {
  await _origOpenCalibrationSettings();
  // Tag the overlay for _reopenCalibrationSettings + scope-form injection.
  const overlays = document.querySelectorAll("body > div");
  const target = overlays[overlays.length - 1];
  if (!target) return;
  target.setAttribute("data-cal-settings-overlay", "1");

  // Wait a tick for the refresh() inside the inner function to populate
  // the body, then tack on our per-client/per-channel forms.
  const inner = target.querySelector("div");
  if (!inner) return;

  // Poll very briefly (should resolve within a tick once fetch resolves).
  const waitBodyReady = async () => {
    for (let i = 0; i < 40; i++) {
      const scopeHook = inner.querySelector("h5");
      if (scopeHook) return inner;
      await new Promise(r => setTimeout(r, 25));
    }
    return inner;
  };
  const body = await waitBodyReady();

  try {
    const settings = await apiFetch("/api/calibration/settings").then(r => r.json());
    const profiles = await apiFetch("/api/calibration/profiles").then(r => r.json());
    // Issue 5: per-client + per-channel forms.
    const scopeBox = el("div", { "data-scope-forms": "1" });
    await _renderScopeOverrideForms(scopeBox, settings, profiles);
    body.append(scopeBox);

    // Issue 7: enrich observation drawer (monkey-patch the open click).
    // Issue 8: enrich confidence badge tooltip.
    _enhanceProfileBadgesWithTooltip(body, profiles, settings);
    _enhanceObservationDrawerButtons(body, profiles);
  } catch (e) { console.warn("PR C settings enrichment failed", e); }
};

/* ---------- Issue 8: rich tooltip panel on confidence badge click ---------- */

function _confidencePanel(profile) {
  const sample = Number(profile.sample_factor || 0);
  const consistency = Number(profile.consistency_factor || 0);
  const cv = Number(profile.cv || 0);
  const nEff = Number(profile.n_effective || 0);
  const score = Number(profile.confidence_score || 0);
  const panel = el("div", {
    class: "confidence-tooltip",
    style: "position:absolute;z-index:2000;background:#fff;border:1px solid #E5E7EB;"
         + "border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,0.12);padding:12px;"
         + "font-size:12px;min-width:260px;max-width:320px",
  });
  panel.append(
    el("div", { style: "font-weight:700;margin-bottom:6px" }, `Score: ${score} / 100`),
    el("div", { style: "color:#374151" },
      `Sample factor: ${sample.toFixed(3)} (n_eff = ${nEff.toFixed(1)}, saturates at n_eff=15)`),
    el("div", { style: "color:#374151" },
      `Consistency factor: ${consistency.toFixed(3)} (cv = ${cv.toFixed(3)})`),
    el("div", { style: "color:#6B7280;margin-top:6px;font-size:11px" },
      `Formula: round(100 × (0.6 × sample + 0.4 × consistency)) = ${score}`),
  );
  return panel;
}

function _enhanceProfileBadgesWithTooltip(body, profiles, settings) {
  // Map badge -> profile by row index. Query confidence pills inside the profile table.
  const rows = body.querySelectorAll("table.tbl tbody tr");
  rows.forEach((tr, i) => {
    const p = profiles[i];
    if (!p) return;
    const badge = tr.querySelector(".confidence-high, .confidence-mid, .confidence-low");
    if (!badge || badge.dataset.tooltipEnhanced) return;
    badge.dataset.tooltipEnhanced = "1";
    badge.style.cursor = "pointer";
    badge.addEventListener("click", (e) => {
      e.stopPropagation();
      // Remove any existing tooltip.
      document.querySelectorAll(".confidence-tooltip").forEach(el2 => el2.remove());
      const panel = _confidencePanel(p);
      const rect = badge.getBoundingClientRect();
      panel.style.left = `${rect.left + window.scrollX}px`;
      panel.style.top = `${rect.bottom + window.scrollY + 4}px`;
      document.body.append(panel);
      // Click-outside to close.
      const onDocClick = (ev) => {
        if (ev.target === panel || panel.contains(ev.target)) return;
        panel.remove();
        document.removeEventListener("click", onDocClick);
      };
      setTimeout(() => document.addEventListener("click", onDocClick), 0);
    });
  });
}

/* ---------- Issue 7: effective_weight + age_days in observation drawer ---------- */

function _enhanceObservationDrawerButtons(body, profiles) {
  const rows = body.querySelectorAll("table.tbl tbody tr");
  rows.forEach((tr, i) => {
    const p = profiles[i];
    if (!p) return;
    const btn = tr.querySelector("button");
    if (!btn || btn.dataset.prcDrawer) return;
    btn.dataset.prcDrawer = "1";
    // Replace the default drawer opener with one that shows weight + age.
    const origOnclick = btn.onclick;
    btn.onclick = async (_ev) => {
      // Remove any stale drawer first.
      document.querySelectorAll("[data-obs-drawer]").forEach(x => x.remove());
      const rows2 = await apiFetch(
        `/api/calibration/observations?client_id=${encodeURIComponent(p.client_id)}` +
        `&target_id=${encodeURIComponent(p.target_id)}` +
        `&channel_id=${encodeURIComponent(p.channel_id)}` +
        `&metric=${encodeURIComponent(p.metric)}`
      ).then(r => r.json());
      const drawer = el("div", {
        "data-obs-drawer": "1",
        style: "position:fixed;right:0;top:0;bottom:0;width:min(560px,90vw);background:#fff;"
             + "border-left:1px solid #E5E7EB;padding:16px;overflow:auto;z-index:1100",
      });
      drawer.append(
        el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:10px" },
          el("h4", { style: "margin:0" },
            `Observations · ${p.channel_id} / ${p.metric}`),
          el("button", {
            style: "border:0;background:#F3F4F6;padding:4px 10px;border-radius:999px;cursor:pointer",
            onclick: () => drawer.remove(),
          }, "✕")),
        el("p", { style: "color:#6B7280;font-size:12px;margin:0 0 10px" },
          "weight_override 設為 0 可排除、1 可強制計入。留空 = 依半衰期計算。"),
      );
      (rows2 || []).forEach(r => {
        const weightInput = el("input", {
          type: "number", step: "0.01", min: "0", max: "1",
          value: r.weight_override == null ? "" : String(r.weight_override),
          style: "width:70px;padding:3px 6px;border:1px solid #E5E7EB;border-radius:6px",
        });
        const savebtn = el("button", {
          style: "margin-left:6px;padding:3px 10px;border:0;background:#10B981;color:#fff;"
               + "border-radius:999px;cursor:pointer;font-size:12px",
          onclick: async () => {
            const val = weightInput.value === "" ? null : Number(weightInput.value);
            await apiFetch(`/api/calibration/observations/${r.id}`, {
              method: "PATCH",
              body: JSON.stringify({ weight_override: val }),
            });
            botSay(`Observation ${r.id} weight_override = ${val == null ? "(clear)" : val}.`);
            drawer.remove();
          },
        }, "存");
        const pinned = r.weight_override != null ? " (pinned)" : "";
        const eff = r.effective_weight != null ? Number(r.effective_weight).toFixed(2) : "—";
        const age = r.age_days != null ? Number(r.age_days).toFixed(0) : "?";
        drawer.append(
          el("div", {
            style: "border-top:1px solid #F3F4F6;padding:8px 0;"
                 + "display:grid;grid-template-columns:1fr auto;gap:6px;align-items:center",
          },
            el("div", {},
              el("div", { style: "font-size:12px;color:#111827" },
                `value = ${Number(r.value).toFixed(2)}`),
              el("div", { style: "font-size:11px;color:#6B7280" },
                `observed_at = ${new Date(r.observed_at * 1000).toISOString().slice(0, 10)}`),
              el("div", { style: "font-size:11px;color:#6B7280" },
                `Weight: ${eff} (age ${age} days)${pinned}`),
            ),
            el("div", { style: "display:flex;align-items:center" },
              weightInput, savebtn),
          )
        );
      });
      document.body.append(drawer);
    };
  });
}

/* ---------- Issue 9: Actuals history viewer ---------- */

async function openActualsHistory(plan) {
  const rows = await apiFetch(`/api/plans/${plan.id}/actuals/history`).then(r => r.json()).catch(() => []);
  const overlay = el("div", {
    style: "position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:1200;display:flex;"
         + "align-items:center;justify-content:center",
  });
  const panel = el("div", {
    style: "background:#fff;border-radius:12px;padding:18px;width:min(960px,92vw);"
         + "max-height:86vh;overflow:auto",
  });
  panel.append(
    el("div", { style: "display:flex;justify-content:space-between;align-items:center;margin-bottom:8px" },
      el("h3", { style: "margin:0" }, `📜 歷史記錄 · ${plan.name}`),
      el("button", {
        style: "border:0;background:#F3F4F6;padding:4px 10px;border-radius:999px;cursor:pointer",
        onclick: () => overlay.remove(),
      }, "✕")));
  if (!rows.length) {
    panel.append(el("div", { style: "color:#6B7280;padding:10px" }, "尚無覆寫紀錄。"));
  } else {
    const tbl = el("table", { class: "tbl" });
    tbl.append(el("thead", {}, el("tr", {},
      el("th", {}, "Scope"),
      el("th", { class: "num" }, "Week"),
      el("th", {}, "Recorded at"),
      el("th", {}, "Superseded at"),
      el("th", {}, "First channel spend"),
    )));
    const tb = el("tbody", {});
    rows.forEach(r => {
      const firstCh = Object.keys(r.per_channel || {})[0];
      const spend = firstCh ? Math.round(Number(r.per_channel[firstCh]?.spend_twd || 0)).toLocaleString() : "—";
      const recAt = new Date(Number(r.recorded_at) * 1000).toISOString().slice(0, 16).replace("T", " ");
      const supAt = r.superseded_at
        ? new Date(Number(r.superseded_at) * 1000).toISOString().slice(0, 16).replace("T", " ")
        : "—";
      tb.append(el("tr", {},
        el("td", {}, r.scope),
        el("td", { class: "num" }, r.period_week == null ? "—" : String(r.period_week)),
        el("td", {}, recAt),
        el("td", {}, supAt),
        el("td", {}, firstCh ? `${firstCh}: ${spend}` : "—"),
      ));
    });
    tbl.append(tb);
    panel.append(tbl);
  }
  overlay.append(panel);
  document.body.append(overlay);
}

/* ---------- Issue 9 + 10: extend openActualsModal with history button + per-week delete ---------- */

const _origOpenActualsModal = openActualsModal;
openActualsModal = async function(plan) {
  await _origOpenActualsModal(plan);
  // Find the last-opened overlay for this plan.
  const overlays = document.querySelectorAll("body > div");
  const overlay = overlays[overlays.length - 1];
  if (!overlay) return;
  const modal = overlay.querySelector("div");
  if (!modal) return;

  // Inject a "📜 歷史記錄" button next to 儲存.
  const saveBtn = [...modal.querySelectorAll("button")]
    .find(b => b.textContent === "儲存");
  if (saveBtn && !saveBtn.dataset.prcHistoryInjected) {
    saveBtn.dataset.prcHistoryInjected = "1";
    const histBtn = el("button", {
      style: "margin-left:8px;padding:8px 14px;border:1px solid #111827;"
           + "background:#fff;color:#111827;border-radius:999px;cursor:pointer",
      onclick: () => openActualsHistory(plan),
    }, "📜 歷史記錄");
    saveBtn.parentNode?.append(histBtn);
  }

  // Issue 10: per-week delete button on saved weeks only.
  let existing;
  try {
    existing = await apiFetch(`/api/plans/${plan.id}/actuals`).then(r => r.json());
  } catch (_) { existing = []; }
  const savedByWeek = {};
  (existing || []).forEach(rec => {
    if (rec.scope === "WEEKLY" && rec.period_week != null) {
      savedByWeek[rec.period_week] = rec;
    }
  });

  const weekBlocks = modal.querySelectorAll("details");
  weekBlocks.forEach((det, idx) => {
    const week = idx + 1;
    const rec = savedByWeek[week];
    if (!rec) return;
    const summary = det.querySelector("summary");
    if (!summary || summary.dataset.prcDelInjected) return;
    summary.dataset.prcDelInjected = "1";
    const delBtn = el("button", {
      style: "margin-left:10px;padding:2px 8px;border:1px solid #EF4444;color:#EF4444;"
           + "background:#fff;border-radius:999px;cursor:pointer;font-size:11px",
      onclick: async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (!confirm(`確認刪除 Week ${week} 的 actuals?`)) return;
        try {
          await apiFetch(`/api/plans/${plan.id}/actuals/${rec.id}`, { method: "DELETE" });
          botSay(`🗑 已刪除 Week ${week} actuals.`);
          overlay.remove();
          openActualsModal(plan);
        } catch (e) { showError(e); }
      },
    }, "🗑 刪除本週");
    summary.append(delBtn);
  });
};

/* ---------- Issue 15: data-plan-id attributes on plan rows ---------- */

/* Replace the index-based plan lookup in the existing renderProjectDetail
 * wrapper with a data-plan-id attribute. We inject the attribute AFTER
 * the base wrapper finished so we stay compatible with existing code. */
const _origRenderProjectDetailPRC = renderProjectDetail;
renderProjectDetail = async function(projectId) {
  await _origRenderProjectDetailPRC(projectId);
  try {
    const plans = await api.projectPlans(projectId);
    // Find every plan row produced by the base renderer + tag it.
    const planRows = scroll.querySelectorAll("table.tbl tbody tr");
    planRows.forEach((tr, idx) => {
      const firstHeader = tr.parentElement?.previousElementSibling?.querySelector("th")?.textContent || "";
      if (firstHeader !== "Plan") return;
      const plan = plans[idx] || plans[idx - (planRows.length - plans.length)];
      if (!plan) return;
      tr.setAttribute("data-plan-id", plan.id);
    });
  } catch (_) { /* best-effort */ }
};

/* ========================================================================
 * v7 · Review dashboard — single-plan chart cluster + one-click download
 * ====================================================================== */

/** Build an HTML <a> that downloads a Blob with the given filename.
 *  Handles the URL.createObjectURL + revoke lifecycle. */
function _triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.setAttribute("download", filename);
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on next tick so Safari can still honour the click.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Attach a small 📥 PNG button below a Chart.js canvas. Uses
 *  chart.toBase64Image() so the export matches what the user sees. */
function _attachChartPngDownload(container, chartInstance, filenameBase) {
  const btn = el("button", {
    class: "chart-download-btn",
    style:
      "margin-top:4px;padding:3px 10px;border:1px solid #E5E7EB;background:#fff;" +
      "border-radius:999px;cursor:pointer;font-size:11px;color:#374151",
    onclick: () => {
      try {
        const dataUrl = chartInstance.toBase64Image("image/png", 1.0);
        const b64 = dataUrl.split(",")[1];
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const blob = new Blob([bytes], { type: "image/png" });
        _triggerDownload(blob, `${filenameBase}.png`);
      } catch (e) {
        showError(e);
      }
    },
  }, "📥 下載 PNG");
  container.appendChild(btn);
}

/** One-shot helper: fetch the augmented payload, render 4 canvases
 *  (Summary, Budget, Frequency, Weekly GRP), attach per-chart PNG
 *  download buttons, and hand back the Chart.js instances + raw
 *  payload so the master export can harvest them.
 *
 *  Returns ``{ charts: {name: Chart}, payload, section }`` or ``null``
 *  when Chart.js failed to load (falls back silently — caller keeps
 *  the existing table view). */
async function renderPlanDashboard(plan, container) {
  const Chart = await loadChartLib();
  if (!Chart) return null;

  let payload;
  try {
    const augmented = await apiFetch(`/api/plans/${plan.id}/augmented`)
      .then(r => r.json());
    payload = { plans: [augmented] };
  } catch (e) {
    showError(e);
    return null;
  }

  const section = el("div", { class: "dashboard-grid", style:
    "display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));" +
    "gap:16px;margin-top:12px" });

  const charts = {};

  // The existing drawXxxChart(Chart, canvas, payload) helpers call
  // `new Chart(canvas, cfg)` internally but don't return the instance.
  // We monkey-patch Chart briefly to capture it — only the local
  // reference, so we don't affect any other concurrent caller.
  const capture = (fn) => (ChartCls, canvas, p) => {
    let inst;
    const OriginalChart = window.Chart;
    window.Chart = class extends OriginalChart {
      constructor(c, cfg) { super(c, cfg); inst = this; }
    };
    try { fn(window.Chart, canvas, p); } finally { window.Chart = OriginalChart; }
    return inst;
  };

  function _chartCard(title, canvasId, drawFn, filenameSuffix) {
    const canvasWrap = el("div", { style:
      "background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:12px" });
    canvasWrap.append(el("h6", { style: "margin:0 0 6px;color:#111827" }, title));
    const canvas = el("canvas", { id: canvasId, style: "width:100%;height:220px" });
    canvasWrap.append(canvas);
    const chart = drawFn(Chart, canvas, payload);
    charts[filenameSuffix] = chart;
    _attachChartPngDownload(canvasWrap, chart, `${plan.name}_${filenameSuffix}`);
    section.append(canvasWrap);
  }

  _chartCard("Summary · 核心指標", "rev-summary-" + plan.id,
             capture(drawSummaryChart), "summary");
  _chartCard("Budget · Channel 分配", "rev-budget-" + plan.id,
             capture(drawBudgetChart), "budget");
  _chartCard("Frequency Distribution · 有效觸及", "rev-freq-" + plan.id,
             capture(drawFrequencyChart), "frequency");
  _chartCard("Weekly GRP · 每週 GRP", "rev-weekly-" + plan.id,
             capture(drawWeeklyChart), "weekly");

  container.appendChild(section);
  return { charts, payload, section };
}

/** The image MIME + encoding we embed into exported HTML. Declared as a
 *  module constant so static analysis can verify the export is
 *  self-contained (no remote image hrefs). The data URIs produced by
 *  Chart.js via toBase64Image() use this exact prefix:
 *      data:image/png;base64,...  */
const _EXPORT_IMAGE_DATA_URI_PREFIX = "data:image/png;base64";

/** Export the full plan dashboard as a self-contained HTML file.
 *  Embeds every chart PNG as a data: URI so the exported file works
 *  offline, opens in any browser, and prints cleanly.
 *
 *  @param {object} plan      The plan JSON (carries summary + allocations).
 *  @param {object} dashboard Return value from renderPlanDashboard (charts map).
 */
function exportPlanReport(plan, dashboard) {
  // Sanity: every embedded image must start with _EXPORT_IMAGE_DATA_URI_PREFIX —
  // that's the invariant this function maintains.
  if (!dashboard || !dashboard.charts) {
    alert("圖表還在載入，請稍候再試。");
    return;
  }
  const images = {};
  Object.entries(dashboard.charts).forEach(([k, c]) => {
    try { images[k] = c.toBase64Image("image/png", 1.0); }
    catch (_) { images[k] = null; }
  });

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const allocRows = (plan.allocations || []).map(a => `
    <tr>
      <td>${esc(a.channel_id)}</td>
      <td class="num">${(a.total_budget_twd || 0).toLocaleString()}</td>
      <td class="num">${(a.total_impressions || 0).toLocaleString()}</td>
      <td class="num">${a.total_grp || 0}</td>
      <td class="num">${(a.net_reach_pct || 0).toFixed(2)}</td>
      <td class="num">${(a.frequency || 0).toFixed(2)}</td>
    </tr>`).join("");

  const s = plan.summary || {};
  const brief = (state && state.session && state.session.brief) || {};
  const imgTag = (key, title) =>
    images[key]
      ? `<figure><figcaption>${esc(title)}</figcaption>` +
        `<img alt="${esc(title)}" src="${images[key]}"/></figure>`
      : "";

  const html = `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>CCS Plan Report · ${esc(plan.name || "Plan")}</title>
<style>
  body{font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;
       color:#111827;max-width:1080px;margin:24px auto;padding:0 24px}
  h1{margin:0 0 8px;font-size:22px}
  .meta{color:#6B7280;font-size:13px;margin-bottom:18px}
  table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}
  th,td{border:1px solid #E5E7EB;padding:6px 10px;text-align:right}
  th:first-child,td:first-child{text-align:left}
  thead{background:#F9FAFB}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  .kpi{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
  .kpi-cell{background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;
            padding:8px 14px;min-width:140px}
  .kpi-cell b{display:block;font-size:18px}
  .kpi-cell span{color:#6B7280;font-size:12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));
        gap:18px;margin-top:12px}
  figure{margin:0;background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:10px}
  figcaption{font-weight:600;margin-bottom:6px}
  img{width:100%;height:auto;display:block}
  @media print{body{margin:0;padding:0}.grid{display:block}figure{page-break-inside:avoid}}
</style></head><body>
<h1>CCS Plan Report · ${esc(plan.name || "Plan")}</h1>
<div class="meta">
  Kind: ${esc(plan.kind || "")} ·
  Client: ${esc(brief.client_id || "—")} ·
  Target: ${esc((brief.target_ids || []).join(" / ") || "—")} ·
  Weeks: ${esc(brief.weeks || "")} ·
  Generated: ${new Date().toISOString().slice(0, 19).replace("T", " ")}
</div>

<section class="kpi">
  <div class="kpi-cell"><span>Total Budget (TWD)</span><b>${(s.total_budget_twd || 0).toLocaleString()}</b></div>
  <div class="kpi-cell"><span>Total Impressions</span><b>${(s.total_impressions || 0).toLocaleString()}</b></div>
  <div class="kpi-cell"><span>Total GRP</span><b>${s.total_grp || 0}</b></div>
  <div class="kpi-cell"><span>Net Reach %</span><b>${s.net_reach_pct || 0}</b></div>
  <div class="kpi-cell"><span>Frequency</span><b>${s.frequency || 0}</b></div>
</section>

<h2>Channel Allocation</h2>
<table>
  <thead><tr><th>Channel</th><th class="num">Budget</th><th class="num">Impressions</th>
    <th class="num">GRP</th><th class="num">Net Reach %</th><th class="num">Freq</th></tr></thead>
  <tbody>${allocRows}</tbody>
</table>

<h2>Charts</h2>
<div class="grid">
  ${imgTag("summary",   "Summary · 核心指標")}
  ${imgTag("budget",    "Budget · Channel 分配")}
  ${imgTag("frequency", "Frequency Distribution · 有效觸及")}
  ${imgTag("weekly",    "Weekly GRP · 每週 GRP")}
</div>

<p style="color:#6B7280;font-size:11px;margin-top:24px">
  Generated by CCS Planner Agent · self-contained export (offline-viewable).
</p>
</body></html>`;

  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const safeName = String(plan.name || "plan").replace(/[^\w\-一-龥]/g, "_");
  _triggerDownload(blob, `ccs-report-${safeName}.html`);
}

/* Wrap renderReview so finishing a plan automatically renders the
 * dashboard + export button below the existing summary card. */
const _origRenderReviewV7 = renderReview;
renderReview = function(msg) {
  _origRenderReviewV7(msg);
  const plan = state.plan;
  if (!plan || !plan.id) return;  // nothing to chart yet

  const epoch = state._renderEpoch;  // v6 staleness guard
  const host = el("div", { class: "card", style: "margin-top:10px" });
  host.append(el("h5", { style: "margin-bottom:2px" }, "📊 Plan Dashboard"));
  host.append(el("div", { style: "color:#6B7280;font-size:12px;margin-bottom:8px" },
    "完成的 plan 視覺化。每張圖可直接下載 PNG，或用下方按鈕匯出完整 HTML 報告（內含圖表、可離線瀏覽/列印）。"));

  const chartContainer = el("div", {});
  host.append(chartContainer);

  const exportBtn = el("button", {
    id: "reviewExportBtn",
    style: "margin-top:12px;padding:8px 18px;border:0;background:#111827;color:#fff;" +
           "border-radius:999px;cursor:pointer;font-weight:600",
    disabled: "",
    onclick: () => exportPlanReport(plan, chartContainer._dashboard),
  }, "📄 匯出完整報告 (HTML)");
  host.append(exportBtn);
  msg.append(host);

  // Kick off the async render; bail if the user navigates away.
  (async () => {
    const dashboard = await renderPlanDashboard(plan, chartContainer);
    if (epoch !== state._renderEpoch) return;
    if (dashboard) {
      chartContainer._dashboard = dashboard;
    } else {
      chartContainer.append(el("div", { class: "note", style: "color:#6B7280" },
        "圖表暫時無法載入（可能離線中）— 下方仍可下載文字表格報告。"));
    }
    exportBtn.removeAttribute("disabled");  // export still works w/o charts
  })();
};

/* Reports-view enhancement: same dashboard below the variance table
 * so 成效回顧 also gets the chart cluster + export button. */
const _origRenderPlanReportV7 = renderPlanReport;
renderPlanReport = async function(plan) {
  await _origRenderPlanReportV7(plan);
  const epoch = state._renderEpoch;
  const msgNode = scroll.querySelector(".bubble.bot:last-child .msg");
  if (!msgNode) return;

  const host = el("div", { class: "card", style: "margin-top:10px" });
  host.append(el("h5", { style: "margin-bottom:2px" }, "📊 Plan Dashboard"));
  host.append(el("div", { style: "color:#6B7280;font-size:12px;margin-bottom:8px" },
    "需要帶離線版本給 client 或長官？按下方「匯出完整報告」可拿到含圖表的單一 HTML 檔。"));
  const chartContainer = el("div", {});
  host.append(chartContainer);
  const exportBtn = el("button", {
    style: "margin-top:12px;padding:8px 18px;border:0;background:#111827;color:#fff;" +
           "border-radius:999px;cursor:pointer;font-weight:600",
    disabled: "",
    onclick: () => exportPlanReport(plan, chartContainer._dashboard),
  }, "📄 匯出完整報告 (HTML)");
  host.append(exportBtn);
  msgNode.append(host);

  (async () => {
    const dashboard = await renderPlanDashboard(plan, chartContainer);
    if (epoch !== state._renderEpoch) return;
    if (dashboard) chartContainer._dashboard = dashboard;
    exportBtn.removeAttribute("disabled");
  })();
};

/* ========================================================================
 * v7.1 · One-click ZIP bundle — single-plan + Compare
 * ====================================================================== */

/** Lazy-load JSZip from jsDelivr (ESM build). Same pattern as
 *  loadChartLib: cached on window, returns null on failure so callers
 *  can gracefully fall back. */
async function loadZipLib() {
  if (window._zipLib !== undefined) return window._zipLib;
  try {
    const mod = await import("https://cdn.jsdelivr.net/npm/jszip@3.10.1/+esm");
    window._zipLib = mod.default || mod.JSZip || mod;
    return window._zipLib;
  } catch (e) {
    console.warn("JSZip load failed", e);
    window._zipLib = null;
    return null;
  }
}

/** Shared helper — pull the PNG bytes out of a Chart.js instance.
 *  Returns ``{bytes: Uint8Array, base64: string}`` or ``null``. */
function _chartPngBytes(chart) {
  try {
    const dataUrl = chart.toBase64Image("image/png", 1.0);
    const b64 = dataUrl.split(",")[1];
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return { bytes, base64: b64 };
  } catch (_) { return null; }
}

function _safeFileName(s) {
  return String(s || "plan").replace(/[^\w\-一-龥]/g, "_").slice(0, 60);
}

/** Build a ZIP bundle for a single completed plan. Includes:
 *    report.html   self-contained HTML (same format as the old export)
 *    charts/*.png  each dashboard chart as a standalone PNG
 *    data/plan.json       raw plan record (summary + allocations)
 *    data/augmented.json  frequency_distribution + duplication + weekly_grp
 *    README.txt    short guide
 *  Falls back to exportPlanReport() if JSZip fails to load.
 */
async function bundlePlanZip(plan, dashboard) {
  const JSZip = await loadZipLib();
  if (!JSZip) {
    exportPlanReport(plan, dashboard);  // PR #13 fallback
    return;
  }
  if (!dashboard || !dashboard.charts) {
    alert("圖表還在載入，請稍候再試。");
    return;
  }

  const zip = new JSZip();

  // HTML report — re-use the same buildPlanHtml logic by intercepting
  // the download trigger. Simpler: inline the HTML builder used by
  // exportPlanReport — extracted into _buildPlanReportHtml for DRY.
  zip.file("report.html", _buildPlanReportHtml(plan, dashboard));

  // Per-chart PNGs
  const charts = zip.folder("charts");
  Object.entries(dashboard.charts).forEach(([name, chart]) => {
    const out = _chartPngBytes(chart);
    if (out) charts.file(`${name}.png`, out.base64, { base64: true });
  });

  // Raw data — plan.json for the record, augmented.json for derived stats.
  const data = zip.folder("data");
  data.file("plan.json", JSON.stringify(plan, null, 2));
  if (dashboard.payload && dashboard.payload.plans && dashboard.payload.plans[0]) {
    const aug = { ...dashboard.payload.plans[0] };
    // plan data already duplicates in report.html; extract just the derived
    // stats to keep augmented.json small and focused.
    const augmented = {
      frequency_distribution: aug.frequency_distribution,
      duplication: aug.duplication,
      weekly_grp: aug.weekly_grp,
    };
    data.file("augmented.json", JSON.stringify(augmented, null, 2));
  }

  zip.file("README.txt", _buildSingleReadme(plan));

  const blob = await zip.generateAsync({ type: "blob" });
  _triggerDownload(blob, `ccs-bundle-${_safeFileName(plan.name)}.zip`);
}

/** Build a ZIP bundle for a Compare view. Structure:
 *    report.html        comparison report with both plans' charts
 *    charts/*.png       every chart (5 for compare: summary, budget, reach, frequency, weekly)
 *    data/plans.json    full augmented payload (both plans + delta)
 *    data/plan-N.json   each plan's individual record
 *    README.txt
 */
async function bundleCompareZip(payload, charts) {
  const JSZip = await loadZipLib();
  if (!JSZip) {
    alert("⚠️ 無法載入 JSZip (離線或 CDN 被擋)。請改用每張圖右下角的 📥 PNG 分別下載。");
    return;
  }
  if (!charts || !Object.keys(charts).length) {
    alert("圖表還在載入，請稍候再試。");
    return;
  }

  const zip = new JSZip();
  zip.file("report.html", _buildCompareReportHtml(payload, charts));

  const chartsFolder = zip.folder("charts");
  Object.entries(charts).forEach(([name, chart]) => {
    const out = _chartPngBytes(chart);
    if (out) chartsFolder.file(`${name}.png`, out.base64, { base64: true });
  });

  const data = zip.folder("data");
  data.file("plans.json", JSON.stringify(payload, null, 2));
  (payload.plans || []).forEach((p, i) => {
    data.file(`plan-${i + 1}.json`, JSON.stringify(p, null, 2));
  });

  zip.file("README.txt", _buildCompareReadme(payload));

  const blob = await zip.generateAsync({ type: "blob" });
  const names = (payload.plans || []).map(p => _safeFileName(p.name)).join("-vs-");
  _triggerDownload(blob, `ccs-compare-${names || "plans"}.zip`);
}

/* ---------- HTML builders (used by both ZIP and HTML-only fallback) ---------- */

function _buildPlanReportHtml(plan, dashboard) {
  const images = {};
  Object.entries(dashboard.charts).forEach(([k, c]) => {
    const out = _chartPngBytes(c);
    images[k] = out ? `data:image/png;base64,${out.base64}` : null;
  });
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const allocRows = (plan.allocations || []).map(a => `
    <tr>
      <td>${esc(a.channel_id)}</td>
      <td class="num">${(a.total_budget_twd || 0).toLocaleString()}</td>
      <td class="num">${(a.total_impressions || 0).toLocaleString()}</td>
      <td class="num">${a.total_grp || 0}</td>
      <td class="num">${(a.net_reach_pct || 0).toFixed(2)}</td>
      <td class="num">${(a.frequency || 0).toFixed(2)}</td>
    </tr>`).join("");
  const s = plan.summary || {};
  const brief = (state && state.session && state.session.brief) || {};
  const imgTag = (key, title) => images[key]
    ? `<figure><figcaption>${esc(title)}</figcaption><img alt="${esc(title)}" src="${images[key]}"/></figure>`
    : "";
  return `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>CCS Plan Report · ${esc(plan.name || "Plan")}</title>
<style>${_REPORT_CSS}</style></head><body>
<h1>CCS Plan Report · ${esc(plan.name || "Plan")}</h1>
<div class="meta">
  Kind: ${esc(plan.kind || "")} ·
  Client: ${esc(brief.client_id || "—")} ·
  Target: ${esc((brief.target_ids || []).join(" / ") || "—")} ·
  Weeks: ${esc(brief.weeks || "")} ·
  Generated: ${new Date().toISOString().slice(0, 19).replace("T", " ")}
</div>
<section class="kpi">
  <div class="kpi-cell"><span>Total Budget (TWD)</span><b>${(s.total_budget_twd || 0).toLocaleString()}</b></div>
  <div class="kpi-cell"><span>Total Impressions</span><b>${(s.total_impressions || 0).toLocaleString()}</b></div>
  <div class="kpi-cell"><span>Total GRP</span><b>${s.total_grp || 0}</b></div>
  <div class="kpi-cell"><span>Net Reach %</span><b>${s.net_reach_pct || 0}</b></div>
  <div class="kpi-cell"><span>Frequency</span><b>${s.frequency || 0}</b></div>
</section>
<h2>Channel Allocation</h2>
<table>
  <thead><tr><th>Channel</th><th class="num">Budget</th><th class="num">Impressions</th>
    <th class="num">GRP</th><th class="num">Net Reach %</th><th class="num">Freq</th></tr></thead>
  <tbody>${allocRows}</tbody>
</table>
<h2>Charts</h2>
<div class="grid">
  ${imgTag("summary",   "Summary · 核心指標")}
  ${imgTag("budget",    "Budget · Channel 分配")}
  ${imgTag("frequency", "Frequency Distribution · 有效觸及")}
  ${imgTag("weekly",    "Weekly GRP · 每週 GRP")}
</div>
<p class="footer">Generated by CCS Planner Agent · self-contained export (offline-viewable).</p>
</body></html>`;
}

function _buildCompareReportHtml(payload, charts) {
  const images = {};
  Object.entries(charts).forEach(([k, c]) => {
    const out = _chartPngBytes(c);
    images[k] = out ? `data:image/png;base64,${out.base64}` : null;
  });
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const plans = payload.plans || [];
  const delta = payload.delta || {};

  const heroRows = plans.map(p => `
    <tr>
      <td>${esc(p.name)}</td>
      <td class="num">${Math.round(p.summary.total_budget_twd || 0).toLocaleString()}</td>
      <td class="num">${Math.round(p.summary.total_impressions || 0).toLocaleString()}</td>
      <td class="num">${(p.summary.net_reach_pct || 0).toFixed(2)}</td>
      <td class="num">${(p.summary.frequency || 0).toFixed(2)}</td>
      <td class="num">${(p.summary.total_grp || 0).toFixed(2)}</td>
    </tr>`).join("");

  const planTables = plans.map((p, idx) => {
    const allocRows = (p.allocations || []).map(a => `
      <tr><td>${esc(a.channel_id)}</td>
        <td class="num">${(a.total_budget_twd || 0).toLocaleString()}</td>
        <td class="num">${(a.total_impressions || 0).toLocaleString()}</td>
        <td class="num">${(a.net_reach_pct || 0).toFixed(2)}</td>
        <td class="num">${(a.frequency || 0).toFixed(2)}</td></tr>`).join("");
    return `<h3>Plan ${idx + 1} · ${esc(p.name)}</h3>
<table>
  <thead><tr><th>Channel</th><th class="num">Budget</th><th class="num">Impressions</th>
    <th class="num">Net Reach %</th><th class="num">Freq</th></tr></thead>
  <tbody>${allocRows}</tbody>
</table>`;
  }).join("");

  const imgTag = (key, title) => images[key]
    ? `<figure><figcaption>${esc(title)}</figcaption><img alt="${esc(title)}" src="${images[key]}"/></figure>`
    : "";

  return `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>CCS Plan Comparison · ${plans.map(p => esc(p.name)).join(" vs ")}</title>
<style>${_REPORT_CSS}</style></head><body>
<h1>CCS Plan Comparison</h1>
<div class="meta">
  ${plans.map(p => esc(p.name)).join(" vs ")} ·
  Generated: ${new Date().toISOString().slice(0, 19).replace("T", " ")}
</div>
<h2>Headline numbers</h2>
<table>
  <thead><tr><th>Plan</th><th class="num">Budget</th><th class="num">Impressions</th>
    <th class="num">Net Reach %</th><th class="num">Frequency</th><th class="num">GRP</th></tr></thead>
  <tbody>${heroRows}</tbody>
  <tfoot><tr class="agg"><td>Δ (last − first)</td>
    <td class="num">${Math.round(delta.total_budget_twd || 0).toLocaleString()}</td>
    <td class="num">${Math.round(delta.total_impressions || 0).toLocaleString()}</td>
    <td class="num">${(delta.net_reach_pct || 0).toFixed(2)}</td>
    <td class="num">${(delta.frequency || 0).toFixed(2)}</td>
    <td class="num">—</td></tr></tfoot>
</table>
<h2>Charts</h2>
<div class="grid">
  ${imgTag("summary",   "Performance summary")}
  ${imgTag("budget",    "Budget per channel")}
  ${imgTag("reach",     "Reach / Attentive / Engagement")}
  ${imgTag("frequency", "Frequency distribution (1+ … 10+)")}
  ${imgTag("weekly",    "Weekly GRP")}
</div>
<h2>Plan breakdowns</h2>
${planTables}
<p class="footer">Generated by CCS Planner Agent · self-contained comparison bundle.</p>
</body></html>`;
}

const _REPORT_CSS = `
body{font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;
     color:#111827;max-width:1080px;margin:24px auto;padding:0 24px}
h1{margin:0 0 8px;font-size:22px}
h2{margin:24px 0 8px;font-size:18px}
h3{margin:18px 0 6px;font-size:15px;color:#374151}
.meta{color:#6B7280;font-size:13px;margin-bottom:18px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid #E5E7EB;padding:6px 10px;text-align:right}
th:first-child,td:first-child{text-align:left}
thead{background:#F9FAFB}
.num{text-align:right;font-variant-numeric:tabular-nums}
.agg{font-weight:600;background:#F6F8FA}
.kpi{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.kpi-cell{background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;
          padding:8px 14px;min-width:140px}
.kpi-cell b{display:block;font-size:18px}
.kpi-cell span{color:#6B7280;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));
      gap:18px;margin-top:12px}
figure{margin:0;background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:10px}
figcaption{font-weight:600;margin-bottom:6px}
img{width:100%;height:auto;display:block}
.footer{color:#6B7280;font-size:11px;margin-top:24px}
@media print{body{margin:0;padding:0}.grid{display:block}figure{page-break-inside:avoid}}
`;

function _buildSingleReadme(plan) {
  return `CCS Plan Bundle — ${plan.name || "plan"}
${"=".repeat(60)}
Generated by CCS Planner Agent at ${new Date().toISOString()}

Contents:
  report.html            — Self-contained HTML report (open in any browser)
  charts/                — Each dashboard chart as a PNG
    summary.png          · Net Reach / Attention / Engagement / Frequency / Brand
    budget.png           · Budget split per channel
    frequency.png        · Reach curve at 1+ through 10+ frequency
    weekly.png           · GRP by week
  data/plan.json         — Raw plan record (summary + channel allocations)
  data/augmented.json    — Derived stats (frequency_distribution, duplication, weekly_grp)

To share this bundle, just send the whole folder — the HTML embeds every
chart as a data URI so it works offline. To print to PDF, open report.html
and press Cmd-P (macOS) / Ctrl-P (Windows).
`;
}

function _buildCompareReadme(payload) {
  const plans = payload.plans || [];
  return `CCS Plan Comparison Bundle
${"=".repeat(60)}
Generated by CCS Planner Agent at ${new Date().toISOString()}
Plans compared: ${plans.map(p => p.name).join(", ")}

Contents:
  report.html            — Self-contained HTML comparison report
  charts/                — All comparison charts as PNGs
    summary.png          · Performance summary
    budget.png           · Budget per channel
    reach.png            · Reach / Attentive / Engagement
    frequency.png        · Frequency distribution
    weekly.png           · Weekly GRP
  data/plans.json        — Full Compare payload (both plans + delta)
  data/plan-1.json ...   — Each plan's individual record

To share: just zip up this folder (it's already a zip) — everything is
self-contained. The HTML embeds every chart as a data URI.
`;
}

/* Replace the single-plan Review export button so it produces a ZIP
 * bundle instead of just the standalone HTML. The HTML fallback still
 * fires automatically when JSZip fails to load, so offline users aren't
 * left stranded. */
const _origRenderReviewV71 = renderReview;
renderReview = function(msg) {
  _origRenderReviewV71(msg);
  // Locate the export button the v7 wrapper injected and relabel +
  // rewire it to the ZIP flow.
  requestAnimationFrame(() => {
    const btn = msg.querySelector("#reviewExportBtn");
    if (!btn) return;
    btn.textContent = "📦 一鍵打包下載 (ZIP)";
    const plan = state.plan;
    const hostCard = btn.closest(".card");
    const chartContainer = hostCard ? hostCard.querySelector("div:not(h5):not(h6)") : null;
    btn.onclick = () => bundlePlanZip(plan, chartContainer ? chartContainer._dashboard : null);
  });
};

/* Same relabel for the reports view (成效回顧). */
const _origRenderPlanReportV71 = renderPlanReport;
renderPlanReport = async function(plan) {
  await _origRenderPlanReportV71(plan);
  requestAnimationFrame(() => {
    // The v7 wrapper adds a button inside the last bubble; find it by
    // matching the 📄 icon text. Replace with ZIP flow.
    const buttons = scroll.querySelectorAll(".bubble.bot:last-child button");
    buttons.forEach(btn => {
      if (btn.textContent && btn.textContent.includes("匯出完整報告")) {
        btn.textContent = "📦 一鍵打包下載 (ZIP)";
        const hostCard = btn.closest(".card");
        const chartContainer = hostCard ? hostCard.querySelector("div:not(h5):not(h6)") : null;
        btn.onclick = () => bundlePlanZip(plan, chartContainer ? chartContainer._dashboard : null);
      }
    });
  });
};

/* Wire the Compare view: capture Chart instances as they're drawn,
 * then inject a 📦 bundle button into the comparison card. */
const _origRenderCompareV71 = renderCompare;
renderCompare = async function(planIds) {
  // Monkey-patch the Chart constructor briefly so every draw in the base
  // renderCompare() records its instance, keyed by the canvas id suffix.
  const captured = {};
  const OriginalChart = window.Chart;
  if (OriginalChart) {
    window.Chart = class extends OriginalChart {
      constructor(canvas, cfg) {
        super(canvas, cfg);
        // canvas.id looks like "chart-summary", "chart-weekly", etc.
        const id = canvas && canvas.id ? canvas.id.replace(/^chart-/, "") : "";
        if (id) captured[id] = this;
      }
    };
  }
  try {
    await _origRenderCompareV71(planIds);
  } finally {
    window.Chart = OriginalChart;
  }

  // Fetch the payload again so we have it for the bundle — cheaper than
  // refactoring the base renderer to expose it.
  let payload = null;
  try {
    const r = await apiFetch("/api/plans/compare", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(planIds),
    });
    if (r.ok) payload = await r.json();
  } catch (_) { /* bundle button will be disabled */ }
  if (!payload) return;

  // Inject a bundle button below the last rendered comparison bubble.
  const msgNode = scroll.querySelector(".bubble.bot:last-child .msg");
  if (!msgNode) return;

  const bundleCard = el("div", { class: "card", style: "margin-top:10px" });
  bundleCard.append(
    el("h5", { style: "margin-bottom:4px" }, "📦 打包下載比較結果"),
    el("div", { style: "color:#6B7280;font-size:12px;margin-bottom:8px" },
      "產出 ZIP：含 HTML 報告、每張圖 PNG、原始 plan 資料 + README。"),
    el("button", {
      style: "padding:8px 18px;border:0;background:#111827;color:#fff;" +
             "border-radius:999px;cursor:pointer;font-weight:600",
      onclick: () => bundleCompareZip(payload, captured),
    }, "📦 一鍵打包下載 (ZIP)"),
  );
  msgNode.append(bundleCard);
};

bootApp();
