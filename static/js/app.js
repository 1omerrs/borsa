"use strict";

const VIEWS = ["piyasa", "uyarilar", "raporlar"];
const VIEW_TITLE = {
  piyasa: "Piyasa",
  uyarilar: "Uyarılar",
  raporlar: "Raporlar",
};
const CATEGORY = {
  tumu: "Tümü",
  hisse: "Hisse",
  altin: "Altın",
  doviz: "Döviz",
  kripto: "Kripto",
  endeks: "Endeks",
};
const CONDITION = {
  above: "Fiyat üstünde",
  below: "Fiyat altında",
  pct_up: "Günlük yükseliş %",
  pct_down: "Günlük düşüş %",
};
const DELIVERY = {
  sent: "gitti",
  skipped: "ayar yok",
  failed: "hata",
  limited: "sınır",
};

const state = {
  view: "piyasa",
  quotes: [],
  status: {},
  admin: false,
  accessChecked: false,
  filter: "tumu",
  query: "",
  selected: "GRAM.ALTIN",
  range: "3A",
  history: [],
  alerts: [],
  events: [],
  reports: [],
  reportPeriod: "daily",
  settings: null,
  hasPrices: false,
  timer: 0,
  pollError: false,
  tickSig: "",
};

document.addEventListener("DOMContentLoaded", boot);

async function boot() {
  bind();
  const initial = (location.hash || "#piyasa").replace("#", "");
  showView(VIEWS.includes(initial) ? initial : "piyasa");
  await poll();
}

function bind() {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => showView(btn.dataset.view));
  });
  window.addEventListener("hashchange", () => {
    const name = (location.hash || "#piyasa").replace("#", "");
    if (VIEWS.includes(name) && name !== state.view) showView(name);
  });
  document.querySelector("#refresh-btn").addEventListener("click", refreshNow);
  document.querySelector("#strip").addEventListener("click", (event) => {
    const key = event.target.closest("[data-jump]");
    if (!key) return;
    state.selected = key.dataset.jump;
    showView("piyasa");
    renderMarket();
    renderDetail();
  });
  document.querySelector("#search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderMarket();
  });
  document.querySelector("#chips").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-cat]");
    if (!btn) return;
    state.filter = btn.dataset.cat;
    renderFilters();
    renderMarket();
  });
  document.querySelector("#grid").addEventListener("click", (event) => {
    const card = event.target.closest(".card");
    if (!card || card.classList.contains("skeleton")) return;
    state.selected = card.dataset.symbol;
    renderMarket();
    renderDetail();
    if (window.innerWidth < 980) {
      document.querySelector("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  document.querySelector("#alert-form").addEventListener("submit", saveAlert);
  document.querySelector("#alert-list").addEventListener("click", onAlertClick);
  document.querySelector("#event-list").addEventListener("click", onEventClick);
  document.querySelector("#instrument-form").addEventListener("submit", saveInstrument);
  document.querySelector("#inst-category").addEventListener("change", (event) => {
    const map = { hisse: "TRY", endeks: "TRY", doviz: "TRY", altin: "USD", kripto: "USD" };
    document.querySelector("#inst-currency").value = map[event.target.value] || "TRY";
  });
  document.querySelectorAll("[data-period]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.reportPeriod = btn.dataset.period;
      document.querySelectorAll("[data-period]").forEach((item) => {
        item.classList.toggle("on", item === btn);
      });
      renderReports();
    });
  });
  document.querySelector("#report-pick").addEventListener("change", renderReports);
  document.querySelector("#print-report").addEventListener("click", () => window.print());
  document.querySelector("#email-report").addEventListener("click", emailReport);
  document.querySelector("#generate-report").addEventListener("click", generateReport);
  window.addEventListener("resize", () => {
    if (state.view !== "piyasa" || state.history.length < 2) return;
    const sliced = sliceRange(state.history, state.range);
    drawChart(sliced, trendUp(sliced), true);
  });
}

function showView(view) {
  state.view = VIEWS.includes(view) ? view : "piyasa";
  VIEWS.forEach((name) => {
    document.querySelector(`#view-${name}`).hidden = name !== state.view;
  });
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.setAttribute("aria-current", btn.dataset.view === state.view ? "page" : "false");
  });
  if (location.hash !== `#${state.view}`) history.replaceState(null, "", `#${state.view}`);
  document.title = `${VIEW_TITLE[state.view]} · BorsaNabız`;
  if (state.view === "uyarilar") loadAlerts();
  if (state.view === "raporlar") loadReports();
  if (state.view === "piyasa" && state.history.length > 1) {
    const sliced = sliceRange(state.history, state.range);
    drawChart(sliced, trendUp(sliced), true);
  }
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const token = sessionStorage.getItem("adminToken");
  if (token) headers["X-Admin-Token"] = token;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let message = "İstek başarısız";
    try {
      const payload = await response.json();
      message = payload.detail || message;
      if (Array.isArray(message)) message = message.map((item) => item.msg || String(item)).join(" ");
    } catch (_error) {
      message = response.statusText || message;
    }
    throw new Error(typeof message === "string" ? message : "İstek başarısız");
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response.text();
}

let pollLock = false;
async function poll() {
  if (pollLock) return;
  pollLock = true;
  try {
    const [market, status] = await Promise.all([api("/api/market"), api("/api/status")]);
    if (!state.accessChecked) {
      state.admin = (await api("/api/access")).admin;
      state.accessChecked = true;
      applyAccess();
    }
    state.quotes = market.quotes || [];
    state.hasPrices = state.quotes.some((quote) => quote.price != null);
    const previousRun = state.status.last_run;
    state.status = status;
    state.pollError = false;
    paint();
    if (status.last_run && status.last_run !== previousRun) {
      if (state.view === "raporlar") loadReports();
      if (state.view === "uyarilar") loadAlerts();
    }
  } catch (error) {
    if (!state.pollError) {
      state.pollError = true;
      toast(error.message, true);
    }
  } finally {
    pollLock = false;
    schedule();
  }
}

function schedule() {
  clearTimeout(state.timer);
  const wait = state.status.running || !state.hasPrices ? 4000 : 60000;
  state.timer = setTimeout(poll, wait);
}

function paint() {
  renderBanner();
  renderHero();
  renderStatusLine();
  renderTicker();
  renderFilters();
  ensureSelection();
  renderMarket();
  renderDetail();
  fillSymbolSelects();
}

function applyAccess() {
  document.querySelectorAll("[data-admin]").forEach((el) => {
    const controls = el.matches("button, input, select, textarea") ? [el] : [];
    controls.push(...el.querySelectorAll("button, input, select, textarea"));
    controls.forEach((control) => {
      control.disabled = !state.admin;
    });
  });
  document.querySelectorAll("[data-admin-inline]").forEach((el) => {
    el.hidden = !state.admin;
  });
  document.querySelectorAll(".lock-note").forEach((el) => {
    el.hidden = state.admin;
  });
}

async function refreshNow() {
  try {
    const result = await api("/api/refresh", { method: "POST" });
    toast(result.started ? "Fiyatlar yenileniyor" : "Çekim zaten sürüyor");
    state.status.running = true;
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, 1500);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderBanner() {
  const banner = document.querySelector("#banner");
  const samples = state.quotes.filter((quote) => quote.source === "ornek");
  const live = state.quotes.filter((quote) => quote.source && quote.source !== "ornek");
  if (!state.hasPrices) {
    banner.hidden = false;
    banner.textContent = state.status.running ? "Fiyatlar çekiliyor…" : "İlk veri bekleniyor…";
    return;
  }
  if (samples.length && !live.length) {
    banner.hidden = false;
    banner.textContent = "Canlı kaynağa şu an ulaşılamadı. Görünen sayılar örnektir; bu veriden uyarı e-postası çıkmaz.";
    return;
  }
  if (samples.length) {
    banner.hidden = false;
    banner.textContent = `Bazı varlıklar örnek veride: ${samples.map((quote) => quote.name).join(", ")}`;
    return;
  }
  if (state.status.failed) {
    banner.hidden = false;
    banner.textContent = "Bazı semboller bu turda yenilenemedi. Ekranda son başarılı kayıt duruyor.";
    return;
  }
  banner.hidden = true;
}

function renderHero() {
  paintKey("#k-bist", "#k-bist-pct", bySymbol("XU100.IS"));
  paintKey("#k-gram", "#k-gram-pct", bySymbol("GRAM.ALTIN"));
  paintKey("#k-usd", "#k-usd-pct", bySymbol("TRY=X"));
  paintKey("#k-eur", "#k-eur-pct", bySymbol("EURTRY=X"));
  const up = state.quotes.filter((quote) => quote.change_percent > 0).length;
  const down = state.quotes.filter((quote) => quote.change_percent < 0).length;
  setText("#k-board", state.hasPrices ? String(up + down) : "—");
  const sub = document.querySelector("#k-board-sub");
  if (sub) {
    sub.textContent = state.hasPrices ? `${up} yükselen · ${down} düşen` : "yükselen / düşen";
  }
  document.querySelectorAll("[data-jump]").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.jump === state.selected);
  });
}

function paintKey(priceId, pctId, quote) {
  setText(priceId, quote ? fmt(quote.price, quote.decimals) : "—");
  const pct = document.querySelector(pctId);
  if (!pct) return;
  pct.textContent = quote ? fmtPct(quote.change_percent) : "—";
  pct.className = quote && quote.change_percent > 0 ? "up" : quote && quote.change_percent < 0 ? "down" : "";
}

function renderStatusLine() {
  const el = document.querySelector("#status-line");
  if (state.status.running) {
    el.textContent = "Fiyatlar yenileniyor…";
    return;
  }
  if (state.status.last_run) {
    el.textContent = `Son çekim ${fmtTime(state.status.last_run)} · sıradaki yaklaşık ${fmtTime(state.status.next_run)}`;
    return;
  }
  el.textContent = "İlk çekim hazırlanıyor";
}

function renderTicker() {
  const box = document.querySelector("#ticker");
  if (!box) return;
  const sig = state.quotes.map((quote) => `${quote.symbol}:${quote.price}`).join("|");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const items = state.quotes
    .filter((quote) => quote.price != null)
    .map((quote) => {
      const tone = quote.change_percent > 0 ? "up" : quote.change_percent < 0 ? "down" : "";
      return `<span class="tick"><b>${esc(quote.name)}</b> ${fmt(quote.price, quote.decimals)} <em class="${tone}">${fmtPct(quote.change_percent)}</em></span>`;
    })
    .join("");
  if (!items) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<div class="marquee-track"><div class="marquee-group">${items}</div><div class="marquee-group" aria-hidden="true">${items}</div></div>`;
}

function renderFilters() {
  document.querySelectorAll("[data-cat]").forEach((btn) => {
    const cat = btn.dataset.cat;
    const count = cat === "tumu" ? state.quotes.length : state.quotes.filter((quote) => quote.category === cat).length;
    btn.textContent = `${CATEGORY[cat]} ${count}`;
    btn.classList.toggle("on", state.filter === cat);
  });
}

function ensureSelection() {
  const current = state.quotes.find((quote) => quote.symbol === state.selected && quote.price != null);
  if (current) return;
  const gram = state.quotes.find((quote) => quote.symbol === "GRAM.ALTIN" && quote.price != null);
  const first = state.quotes.find((quote) => quote.price != null);
  if (gram || first) state.selected = (gram || first).symbol;
}

function visibleQuotes() {
  const query = state.query.trim().toLocaleLowerCase("tr");
  return state.quotes.filter((quote) => {
    if (state.filter !== "tumu" && quote.category !== state.filter) return false;
    if (!query) return true;
    return quote.name.toLocaleLowerCase("tr").includes(query) || quote.symbol.toLowerCase().includes(query);
  });
}

function renderMarket() {
  const box = document.querySelector("#grid");
  if (!state.hasPrices) {
    box.dataset.sig = "wait";
    box.innerHTML = Array.from({ length: 8 }, () => '<div class="skeleton"></div>').join("");
    return;
  }
  const quotes = visibleQuotes();
  const sig = `${state.filter}|${state.query}|${quotes.map((quote) => quote.symbol).join(",")}`;
  if (!quotes.length) {
    box.dataset.sig = sig;
    box.innerHTML = '<p class="empty">Bu süzgeçte varlık yok.</p>';
    return;
  }
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    box.innerHTML = quotes.map(cardHtml).join("");
    return;
  }
  quotes.forEach((quote) => {
    const card = box.querySelector(`[data-symbol="${CSS.escape(quote.symbol)}"]`);
    if (card) updateCard(card, quote);
  });
}

function cardHtml(quote) {
  const tone = quote.change_percent > 0 ? "up" : quote.change_percent < 0 ? "down" : "";
  const selected = quote.symbol === state.selected ? " selected" : "";
  return `<button type="button" class="card ${tone}${selected}" data-symbol="${esc(quote.symbol)}" data-category="${esc(quote.category)}" aria-pressed="${quote.symbol === state.selected}">
    <span class="badge ${esc(quote.category)}">${esc(CATEGORY[quote.category] || quote.category)}</span>
    <span class="who"><strong class="name">${esc(quote.name)}</strong><span class="symbol">${esc(quote.symbol)}</span></span>
    <span class="price">${fmt(quote.price, quote.decimals)}</span>
    <span class="change ${tone}">${fmtPct(quote.change_percent)}</span>
    <span class="week">${fmtPct(quote.change_percent_7d)}</span>
    <span class="src">${esc(sourceLabel(quote.source))}</span>
  </button>`;
}

function updateCard(card, quote) {
  const tone = quote.change_percent > 0 ? "up" : quote.change_percent < 0 ? "down" : "";
  card.classList.toggle("up", tone === "up");
  card.classList.toggle("down", tone === "down");
  card.classList.toggle("selected", quote.symbol === state.selected);
  card.setAttribute("aria-pressed", quote.symbol === state.selected ? "true" : "false");
  const price = card.querySelector(".price");
  const next = fmt(quote.price, quote.decimals);
  if (price.textContent !== next) {
    price.textContent = next;
    card.classList.remove("flash");
    void card.offsetWidth;
    card.classList.add("flash");
  }
  const change = card.querySelector(".change");
  change.textContent = fmtPct(quote.change_percent);
  change.className = `change ${tone}`;
  card.querySelector(".week").textContent = fmtPct(quote.change_percent_7d);
  card.querySelector(".src").textContent = sourceLabel(quote.source);
}

function renderDetail() {
  const box = document.querySelector("#detail");
  const quote = state.quotes.find((item) => item.symbol === state.selected);
  if (!quote || quote.price == null) {
    if (box.dataset.symbol !== "empty") {
      box.dataset.symbol = "empty";
      box.innerHTML = '<div class="detail-empty"><p>Soldan bir satır seç. Fiyat, düne göre fark ve grafik burada durur.</p></div>';
    }
    return;
  }
  if (box.dataset.symbol !== quote.symbol) {
    box.dataset.symbol = quote.symbol;
    box.innerHTML = detailTemplate(quote);
    bindDetail(box);
    loadHistory(quote.symbol);
  }
  fillDetail(quote);
  applyAccess();
}

function detailTemplate(quote) {
  return `<p class="kicker">${esc(CATEGORY[quote.category] || quote.category)}</p>
    <h3 id="d-name">${esc(quote.name)}</h3>
    <p class="symbol" id="d-symbol">${esc(quote.symbol)}</p>
    <p class="detail-price" id="d-price">${fmt(quote.price, quote.decimals)}</p>
    <p id="d-day"></p>
    <div class="seg">
      <button type="button" data-range="1H" class="${state.range === "1H" ? "on" : ""}">1H</button>
      <button type="button" data-range="1A" class="${state.range === "1A" ? "on" : ""}">1A</button>
      <button type="button" data-range="3A" class="${state.range === "3A" ? "on" : ""}">3A</button>
      <button type="button" data-range="6A" class="${state.range === "6A" ? "on" : ""}">6A</button>
    </div>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
    <div class="stats">
      <div><span>Önceki kapanış</span><strong id="d-prev"></strong></div>
      <div><span>7 gün</span><strong id="d-week"></strong></div>
      <div><span>Hacim</span><strong id="d-vol"></strong></div>
      <div><span>Kaynak</span><strong id="d-source"></strong></div>
    </div>
    <p class="note" id="d-note"></p>
    <p class="muted" id="d-time"></p>
    <button type="button" class="btn ghost" data-hide data-admin-inline>Listeden gizle</button>`;
}

function bindDetail(box) {
  box.querySelectorAll("[data-range]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.range = btn.dataset.range;
      box.querySelectorAll("[data-range]").forEach((item) => item.classList.toggle("on", item.dataset.range === state.range));
      const sliced = sliceRange(state.history, state.range);
      drawChart(sliced, trendUp(sliced));
    });
  });
  const hide = box.querySelector("[data-hide]");
  if (!hide) return;
  hide.addEventListener("click", async () => {
    try {
      await api(`/api/instruments/${encodeURIComponent(state.selected)}`, { method: "DELETE" });
      toast("Listeden gizlendi");
      state.selected = "GRAM.ALTIN";
      box.dataset.symbol = "";
      await reloadMarket();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function fillDetail(quote) {
  setText("#d-price", fmt(quote.price, quote.decimals));
  const day = document.querySelector("#d-day");
  if (day) {
    const diff = quote.change_amount == null ? "" : ` · fark ${fmt(quote.change_amount, quote.decimals)}`;
    day.textContent = `Düne göre ${fmtPct(quote.change_percent)}${diff}`;
    day.className = quote.change_percent > 0 ? "up" : quote.change_percent < 0 ? "down" : "";
  }
  setText("#d-prev", fmt(quote.previous_close, quote.decimals));
  setText("#d-week", fmtPct(quote.change_percent_7d));
  setText("#d-vol", fmtVol(quote.volume));
  setText("#d-source", `${sourceLabel(quote.source)} · ${sessionLabel(quote.market_time)}`);
  setText("#d-time", quote.market_time ? `Piyasa zamanı ${fmtTime(quote.market_time)}` : "");
  const note = document.querySelector("#d-note");
  if (!note) return;
  if (quote.symbol === "GRAM.ALTIN") {
    note.textContent = "Ons altın (USD) ÷ 31,1035 × dolar kuru. Kuyumcu satış fiyatı değil, yaklaşık spot değer.";
  } else {
    note.textContent = "Fark, bir önceki kapanışa göre hesaplanır.";
  }
}

async function loadHistory(symbol) {
  try {
    const data = await api(`/api/history/${encodeURIComponent(symbol)}`);
    if (state.selected !== symbol) return;
    state.history = data.points || [];
    const sliced = sliceRange(state.history, state.range);
    requestAnimationFrame(() => drawChart(sliced, trendUp(sliced)));
  } catch (error) {
    toast(error.message, true);
  }
}

let chartToken = 0;
function drawChart(points, up, instant = false) {
  const canvas = document.querySelector("#chart");
  if (!canvas) return;
  const token = ++chartToken;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let progress = instant || reduce ? 1 : 0;
  function frame() {
    if (token !== chartToken) return;
    if (!canvas.clientWidth) {
      requestAnimationFrame(frame);
      return;
    }
    progress = Math.min(1, progress + (instant ? 1 : 0.045));
    paintChart(canvas, points, progress, up);
    if (progress < 1) requestAnimationFrame(frame);
  }
  frame();
}

function paintChart(canvas, points, progress, up) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = canvas.clientWidth;
  const h = canvas.clientHeight || 280;
  if (!w || !h) return;
  canvas.width = Math.floor(w * dpr);
  canvas.height = Math.floor(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!points || points.length < 2) {
    ctx.fillStyle = "#b7c0b4";
    ctx.font = "14px Manrope, sans-serif";
    ctx.fillText("Grafik için yeterli gün yok.", 16, 32);
    return;
  }
  const pad = { l: 58, r: 12, t: 16, b: 28 };
  const values = points.map((point) => point.close);
  let min = Math.min(...values);
  let max = Math.max(...values);
  const span = max - min || Math.abs(max) * 0.02 || 1;
  min -= span * 0.12;
  max += span * 0.12;
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const xAt = (index) => pad.l + (index / (points.length - 1)) * innerW;
  const yAt = (value) => pad.t + (1 - (value - min) / (max - min)) * innerH;
  const lastIndex = Math.max(1, Math.round((points.length - 1) * progress));
  ctx.font = "12px Manrope, sans-serif";
  ctx.strokeStyle = "rgba(243,239,230,0.08)";
  ctx.fillStyle = "#b7c0b4";
  ctx.lineWidth = 1;
  for (let step = 0; step < 4; step += 1) {
    const value = min + ((max - min) * step) / 3;
    const y = yAt(value);
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(w - pad.r, y);
    ctx.stroke();
    ctx.fillText(fmt(value, value >= 1000 ? 0 : 2), 4, y + 4);
  }
  const color = up ? "#3ddc97" : "#ff6d6d";
  const gradient = ctx.createLinearGradient(0, pad.t, 0, pad.t + innerH);
  gradient.addColorStop(0, up ? "rgba(61,220,151,0.35)" : "rgba(255,109,109,0.32)");
  gradient.addColorStop(1, "rgba(9,12,11,0)");
  ctx.beginPath();
  ctx.moveTo(xAt(0), yAt(points[0].close));
  for (let index = 1; index <= lastIndex; index += 1) ctx.lineTo(xAt(index), yAt(points[index].close));
  ctx.lineTo(xAt(lastIndex), pad.t + innerH);
  ctx.lineTo(xAt(0), pad.t + innerH);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(xAt(0), yAt(points[0].close));
  for (let index = 1; index <= lastIndex; index += 1) ctx.lineTo(xAt(index), yAt(points[index].close));
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(xAt(lastIndex), yAt(points[lastIndex].close), 4.5, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.fillStyle = "#b7c0b4";
  ctx.fillText(points[0].day.slice(5), pad.l, h - 8);
  ctx.fillText(points[lastIndex].day.slice(5), Math.max(pad.l, w - pad.r - 48), h - 8);
}

function fillSymbolSelects() {
  const select = document.querySelector("#alert-symbol");
  const sig = state.quotes.map((quote) => quote.symbol).join(",");
  if (select.dataset.sig === sig) return;
  const current = select.value;
  select.dataset.sig = sig;
  select.innerHTML = state.quotes.map((quote) => `<option value="${esc(quote.symbol)}">${esc(quote.name)}</option>`).join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

async function saveAlert(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api("/api/alerts", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.symbol.value,
        condition: form.condition.value,
        threshold: Number(form.threshold.value),
        note: form.note.value.trim(),
      }),
    });
    form.threshold.value = "";
    form.note.value = "";
    toast("Kural kaydedildi");
    await loadAlerts();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadAlerts() {
  try {
    const [rules, events] = await Promise.all([api("/api/alerts"), api("/api/alerts/events")]);
    state.alerts = rules.rules || [];
    state.events = events.events || [];
    renderAlerts();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderAlerts() {
  const list = document.querySelector("#alert-list");
  if (!state.alerts.length) {
    list.innerHTML = '<p class="empty">Henüz kural yok. Örneğin gram altın yüzde 2 düşerse haber ver.</p>';
  } else {
    list.innerHTML = state.alerts.map(ruleCard).join("");
  }
  const events = document.querySelector("#event-list");
  if (!state.events.length) {
    events.innerHTML = '<p class="empty">Henüz tetiklenen uyarı yok.</p>';
  } else {
    events.innerHTML = state.events.map(eventCard).join("");
  }
}

function ruleCard(rule) {
  const unit = rule.condition === "above" || rule.condition === "below" ? "" : "%";
  return `<article class="rule">
    <div>
      <strong>${esc(rule.name)}</strong>
      <p>${esc(CONDITION[rule.condition] || rule.condition)} · ${fmt(rule.threshold, 2)}${unit}</p>
      ${rule.note ? `<p>${esc(rule.note)}</p>` : ""}
    </div>
    <div class="rule-actions">
      <button type="button" data-action="toggle" data-id="${rule.id}" data-enabled="${rule.enabled ? "1" : "0"}">${rule.enabled ? "Durdur" : "Başlat"}</button>
      <button type="button" data-action="delete" data-id="${rule.id}">Sil</button>
    </div>
  </article>`;
}

function eventCard(event) {
  return `<article class="event">
    <div>
      <strong>${esc(event.subject)}</strong>
      <p>${esc(fmtTime(event.created_at))} · E-posta: ${esc(DELIVERY[event.email_status] || event.email_status || "—")} · n8n: ${esc(DELIVERY[event.webhook_status] || event.webhook_status || "—")}</p>
    </div>
    <button type="button" data-retry="${event.id}">Yeniden dene</button>
  </article>`;
}

async function onAlertClick(event) {
  const btn = event.target.closest("button");
  if (!btn) return;
  try {
    if (btn.dataset.action === "delete") {
      await api(`/api/alerts/${btn.dataset.id}`, { method: "DELETE" });
      toast("Kural silindi");
    } else if (btn.dataset.action === "toggle") {
      await api(`/api/alerts/${btn.dataset.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: btn.dataset.enabled !== "1" }),
      });
    }
    await loadAlerts();
  } catch (error) {
    toast(error.message, true);
  }
}

async function onEventClick(event) {
  const btn = event.target.closest("[data-retry]");
  if (!btn) return;
  try {
    const updated = await api(`/api/alerts/events/${btn.dataset.retry}/retry`, { method: "POST" });
    toast(updated.email_status === "sent" ? "E-posta gitti" : "Gönderim denendi");
    await loadAlerts();
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveInstrument(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api("/api/instruments", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.symbol.value,
        name: form.name.value,
        category: form.category.value,
        currency: form.currency.value,
      }),
    });
    form.symbol.value = "";
    form.name.value = "";
    toast("Varlık eklendi. Fiyat sıradaki çekimde gelir.");
    await reloadMarket();
  } catch (error) {
    toast(error.message, true);
  }
}

async function reloadMarket() {
  const market = await api("/api/market");
  state.quotes = market.quotes || [];
  state.hasPrices = state.quotes.some((quote) => quote.price != null);
  paint();
}

async function loadReports() {
  try {
    const data = await api("/api/reports");
    state.reports = data.reports || [];
    renderReports();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderReports() {
  const list = state.reports.filter((report) => report.period === state.reportPeriod);
  const pick = document.querySelector("#report-pick");
  const previous = pick.value;
  pick.innerHTML = list.map((report) => `<option value="${report.id}">${esc(report.title)}</option>`).join("");
  if ([...pick.options].some((option) => option.value === previous)) pick.value = previous;
  const report = list.find((item) => String(item.id) === pick.value) || list[0];
  const body = document.querySelector("#report-body");
  if (!report) {
    body.innerHTML = '<p class="empty">Rapor ilk fiyat çekiminden sonra hazır olur.</p>';
    return;
  }
  const average = report.average_change_percent == null ? "—" : fmtPct(report.average_change_percent);
  const rows = (report.rows || []).map((row) => {
    const tone = row.change_percent > 0 ? "up" : row.change_percent < 0 ? "down" : "";
    return `<tr>
      <td>${esc(row.name)}<div class="symbol">${esc(row.symbol)}</div></td>
      <td>${fmt(row.price, row.decimals ?? 2)}</td>
      <td class="${tone}">${fmt(row.change_amount, row.decimals ?? 2)}</td>
      <td class="${tone}">${fmtPct(row.change_percent)}</td>
      <td>${meter(row.change_percent)}</td>
    </tr>`;
  }).join("");
  body.innerHTML = `
    <h3>${esc(report.title)}</h3>
    <p>${esc(report.summary)}</p>
    <div class="report-stats">
      <div><span>Yükselen</span><strong class="up">${report.advancers}</strong></div>
      <div><span>Düşen</span><strong class="down">${report.decliners}</strong></div>
      <div><span>Yatay</span><strong>${report.unchanged}</strong></div>
      <div><span>Ortalama</span><strong>${average}</strong></div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>Varlık</th><th>Fiyat</th><th>Fark</th><th>Yüzde</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

function meter(pct) {
  if (pct == null) return "";
  const width = Math.max(8, Math.min(100, (Math.abs(pct) / 8) * 100));
  const tone = pct > 0 ? "up" : pct < 0 ? "down" : "";
  return `<div class="meter ${tone}"><span style="width:${width.toFixed(0)}%"></span></div>`;
}

async function emailReport() {
  try {
    await api(`/api/reports/email?period=${state.reportPeriod}`, { method: "POST" });
    toast("Rapor e-postası gitti");
  } catch (error) {
    toast(error.message, true);
  }
}

async function generateReport() {
  try {
    await api("/api/reports/generate", { method: "POST" });
    toast("Rapor güncellendi");
    await loadReports();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadBridge() {
  if (state.admin) {
    try {
      state.settings = await api("/api/settings");
      fillSettings(state.settings);
    } catch (error) {
      toast(error.message, true);
    }
  }
  try {
    const snap = await api("/api/n8n/snapshot");
    const preview = { generated_at: snap.generated_at, rows: (snap.rows || []).slice(0, 1) };
    document.querySelector("#snapshot-preview").textContent = JSON.stringify(preview, null, 2);
  } catch (error) {
    document.querySelector("#snapshot-preview").textContent = error.message;
  }
  renderBridgeStatus();
}

function fillSettings(data) {
  const form = document.querySelector("#settings-form");
  if (!form || !data || form.dataset.dirty === "1") return;
  form.smtp_host.value = data.smtp_host || "";
  form.smtp_port.value = data.smtp_port || 587;
  form.smtp_user.value = data.smtp_user || "";
  form.smtp_from.value = data.smtp_from || "";
  form.alert_email.value = data.alert_email || "";
  form.n8n_webhook_url.value = data.n8n_webhook_url || "";
  form.send_daily_email.checked = Boolean(data.send_daily_email);
  form.fetch_interval_minutes.value = data.fetch_interval_minutes || 15;
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    smtp_host: form.smtp_host.value.trim(),
    smtp_port: Number(form.smtp_port.value),
    smtp_user: form.smtp_user.value.trim(),
    smtp_from: form.smtp_from.value.trim(),
    alert_email: form.alert_email.value.trim(),
    n8n_webhook_url: form.n8n_webhook_url.value.trim(),
    send_daily_email: form.send_daily_email.checked,
    fetch_interval_minutes: Number(form.fetch_interval_minutes.value),
  };
  if (form.smtp_password.value) payload.smtp_password = form.smtp_password.value;
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    form.dataset.dirty = "0";
    form.smtp_password.value = "";
    fillSettings(state.settings);
    toast("Ayarlar kaydedildi");
  } catch (error) {
    toast(error.message, true);
  }
}

async function testEmail() {
  try {
    await api("/api/settings/test-email", { method: "POST" });
    toast("Deneme e-postası gitti");
  } catch (error) {
    toast(error.message, true);
  }
}

async function testWebhook() {
  try {
    await api("/api/settings/test-webhook", { method: "POST" });
    toast("n8n yanıt verdi");
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveToken() {
  const value = document.querySelector("#admin-token").value.trim();
  if (value) sessionStorage.setItem("adminToken", value);
  else sessionStorage.removeItem("adminToken");
  try {
    state.admin = (await api("/api/access")).admin;
    state.accessChecked = true;
    applyAccess();
    toast(state.admin ? "Yönetim açık" : "Anahtar kabul edilmedi");
    if (state.admin) loadBridge();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderBridgeStatus() {
  const box = document.querySelector("#bridge-status");
  if (!box || state.view !== "baglanti") return;
  const failures = (state.status.failures || []).map((item) => esc(item.symbol)).join(", ");
  box.innerHTML = `<strong>Köprü durumu</strong>
    <p>Canlı kayıt: ${state.status.live ?? 0} · örnek: ${state.status.sample ?? 0} · hata: ${state.status.failed ?? 0}</p>
    <p>Aralık: ${state.status.interval_minutes ?? 15} dk</p>
    ${failures ? `<p>Alınamayan: ${failures}</p>` : ""}`;
}

function bySymbol(symbol) {
  return state.quotes.find((quote) => quote.symbol === symbol);
}

function fillSat(selector, label, quote) {
  const el = document.querySelector(selector);
  if (!el) return;
  const tone = quote && quote.change_percent > 0 ? "up" : quote && quote.change_percent < 0 ? "down" : "";
  el.innerHTML = `<span>${esc(label)}</span><strong class="${tone}">${quote ? fmt(quote.price, quote.decimals) : "—"}</strong>`;
}

function sourceLabel(source) {
  if (source === "hesaplanan") return "Hesap";
  if (source === "ornek") return "Örnek";
  if (source === "n8n") return "n8n";
  if (source) return "Canlı";
  return "Bekleniyor";
}

function sessionLabel(iso) {
  if (!iso) return "zaman yok";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "zaman yok";
  const today = new Date();
  return when.toDateString() === today.toDateString() ? "seans içi" : "son kapanış";
}

function sliceRange(points, range) {
  const map = { "1H": 5, "1A": 22, "3A": 66, "6A": 400 };
  return (points || []).slice(-(map[range] || 66));
}

function trendUp(points) {
  if (!points || points.length < 2) return true;
  return points[points.length - 1].close >= points[0].close;
}

function sparkline(values, up) {
  const series = (values || []).map(Number).filter((value) => Number.isFinite(value));
  if (series.length < 2) return "";
  const w = 120;
  const h = 36;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = max - min || 1;
  const d = series.map((value, index) => {
    const x = (index / (series.length - 1)) * w;
    const y = h - 3 - ((value - min) / span) * (h - 6);
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  const color = up ? "#5eae86" : "#d98992";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></svg>`;
}

function fmt(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("tr-TR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${fmt(value, 2)}%`;
}

function fmtVol(value) {
  if (value == null) return "—";
  if (value >= 1e9) return `${fmt(value / 1e9, 2)} Mr`;
  if (value >= 1e6) return `${fmt(value / 1e6, 2)} Mn`;
  if (value >= 1e3) return `${fmt(value / 1e3, 1)} B`;
  return fmt(value, 0);
}

function fmtTime(iso) {
  if (!iso) return "—";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "—";
  return new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(when);
}

function setText(selector, text) {
  const el = document.querySelector(selector);
  if (el) el.textContent = text;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function toast(message, isError = false) {
  const stack = document.querySelector("#toasts");
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Adres kopyalandı");
  } catch (_error) {
    toast("Kopyalanamadı", true);
  }
}
