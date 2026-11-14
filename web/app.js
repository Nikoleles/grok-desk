/* Plan B client. DATA_PATH arrives once as #d= and stays in localStorage. */
(function () {
  const S = {
    screen: "home",
    range: "1D",
    mint: null,
    back: "home",
    dataPath: null,
    state: null,
    chart: null,
    activity: null,
    coin: null,
    loading: true,
    fetchError: false,
    retryIn: 0,
    lastUpdateText: null,
    unreadMuted: false,
    searching: false,
    query: "",
    shownBalance: null,
  };

  const ICONS = {
    home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 11.5 12 4l8 7.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1v-8.5Z"/></svg>',
    agents: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="7" cy="8" r="2.2"/><circle cx="17" cy="8" r="2.2"/><circle cx="12" cy="16" r="2.2"/><path d="M9 9.2 11 14M15 9.2 13 14"/></svg>',
    activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M4 12h16M4 17h10"/></svg>',
    account: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="3"/><path d="M5 19c1.4-3 3.8-4.5 7-4.5S17.6 16 19 19"/></svg>',
    search: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>',
    bell: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.2 2H4.8L6 16Z"/><path d="M10 19a2 2 0 0 0 4 0"/></svg>',
  };

  function parseHash() {
    const out = {};
    const raw = location.hash.replace(/^#/, "");
    if (!raw) return out;
    raw.split("&").forEach((part) => {
      const i = part.indexOf("=");
      if (i < 0) return;
      out[decodeURIComponent(part.slice(0, i))] = decodeURIComponent(part.slice(i + 1));
    });
    return out;
  }

  function readPath() {
    const hash = parseHash();
    if (hash.d) localStorage.setItem("grokdesk.dataPath", hash.d);
    if (hash.t) localStorage.setItem("grokdesk.appToken", hash.t);
    if (hash.screen) S.screen = hash.screen;
    if (hash.mint) S.mint = hash.mint;
    if (hash.range) S.range = hash.range;
    S.dataPath = localStorage.getItem("grokdesk.dataPath");
    if (hash.d || hash.t) {
      const keep = [];
      if (hash.screen) keep.push("screen=" + encodeURIComponent(hash.screen));
      if (hash.mint) keep.push("mint=" + encodeURIComponent(hash.mint));
      if (hash.shot) keep.push("shot=1");
      history.replaceState(null, "", location.pathname + location.search + (keep.length ? "#" + keep.join("&") : ""));
    }
  }

  function reduced() {
    return document.documentElement.classList.contains("shot") ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  async function getJSON(file) {
    const url = new URL("d/" + S.dataPath + "/" + file, document.baseURI);
    const res = await fetch(url.href, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  function clockText(iso, fallback) {
    if (fallback) return fallback;
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  async function poll() {
    if (!S.dataPath) {
      S.loading = false;
      render();
      return;
    }
    try {
      const state = await getJSON("state.json");
      const chart = await getJSON("chart.json");
      const activity = await getJSON("activity.json");
      S.state = state;
      S.chart = chart;
      S.activity = activity;
      if ((S.screen === "coin" || S.screen === "auditor") && S.mint) {
        try { S.coin = await getJSON("coin/" + S.mint + ".json"); }
        catch (err) { S.coin = null; }
      }
      S.fetchError = false;
      S.loading = false;
      S.lastUpdateText = state.lastUpdateText || clockText(state.updatedAt);
      if (state.offline && !document.documentElement.classList.contains("shot")) {
        S.retryIn = state.retryIn || 8;
      }
      render();
    } catch (err) {
      S.fetchError = true;
      S.loading = false;
      if (!S.retryIn) S.retryIn = 5;
      render();
    }
  }

  function tick() {
    if (document.documentElement.classList.contains("shot")) return;
    if (S.fetchError || (S.state && S.state.offline)) {
      S.retryIn = Math.max(0, (S.retryIn || 5) - 1);
      const node = document.querySelector("[data-retry]");
      if (node && S.state) {
        node.textContent = offlineText();
      } else {
        render();
      }
      if (S.retryIn === 0) poll();
    }
  }

  function offlineText() {
    const n = S.retryIn || (S.state && S.state.retryIn) || 5;
    const time = S.lastUpdateText || (S.state && (S.state.lastUpdateText || clockText(S.state.updatedAt))) || "—";
    return "Desk offline · retrying in " + n + "s · last update " + time;
  }

  function go(screen, mint) {
    S.back = S.screen === "coin" || S.screen === "auditor" ? S.back : S.screen;
    S.screen = screen;
    if (mint !== undefined) S.mint = mint;
    S.searching = false;
    if ((screen === "coin" || screen === "auditor") && S.mint) {
      getJSON("coin/" + S.mint + ".json").then((coin) => { S.coin = coin; render(); }).catch(() => { S.coin = null; render(); });
    }
    render();
    window.scrollTo(0, 0);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function tone(value) {
    const text = String(value || "");
    if (text.startsWith("+")) return "up";
    if (text.startsWith("-") || text.startsWith("−")) return "down";
    return "";
  }

  function slider(row) {
    const lo = Number(row.min);
    const hi = Number(row.max);
    const v = Number(row.value);
    let pct = 0;
    if (hi > lo && !Number.isNaN(v)) pct = Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
    if (row.kind === "text" || row.kind === "bool") return "";
    return '<div class="slider" aria-disabled="true"><i style="width:' + pct + '%"></i><b style="left:' + pct + '%"></b></div>';
  }

  function activityRows(rows, empty) {
    if (!rows || !rows.length) return '<p class="empty-copy">' + (empty || "Nothing yet tonight") + "</p>";
    return '<div class="rows">' + rows.map((row) => {
      const dest = row.kind === "rejected" ? "auditor" : "coin";
      return '<button class="act" data-go="' + dest + '" data-mint="' + esc(row.mint) + '">' +
        "<span><b>" + esc(row.title) + "</b><small>" + esc(row.subtitle) + "</small></span>" +
        '<em class="' + tone(row.value) + '">' + esc(row.value) + "</em></button>";
    }).join("") + "</div>";
  }

  function homeCard(card) {
    const lines = (card.lines || []).slice(0, 4);
    let body = "";
    if (card.id === "auditor") {
      body = '<div class="strips">' + (card.strips || []).map((s) => '<i class="strip ' + esc(s) + '"></i>').join("") + "</div>";
    } else if (card.id === "analyst") {
      const score = card.score == null ? "–" : card.score;
      const color = card.score != null && card.score >= 70 ? "#00d35a" : "#ff453a";
      const pct = Math.max(0, Math.min(100, Number(card.score) || 0));
      const c = 2 * Math.PI * 36;
      const dash = (pct / 100) * c;
      body = '<div class="ring-wrap"><svg width="120" height="120" viewBox="0 0 120 120">' +
        '<circle cx="60" cy="60" r="36" stroke="#2c2c2e" stroke-width="8" fill="none"/>' +
        '<circle cx="60" cy="60" r="36" stroke="' + color + '" stroke-width="8" fill="none" stroke-linecap="round" ' +
        'stroke-dasharray="' + dash + ' ' + c + '" transform="rotate(-90 60 60)"/>' +
        '<text x="60" y="66" text-anchor="middle" fill="#fff" font-size="28" font-family="Inter, sans-serif">' + esc(score) + "</text></svg></div>";
    } else if (card.id === "executor") {
      body = '<div class="exec-line">' + esc(card.sizeText || "") + "</div>" +
        '<div class="bar"><i style="width:' + (card.progress || 0) + '%"></i></div>' +
        '<div class="exec-line">' + esc(card.line || "") + "</div>" +
        (card.warning ? '<p class="warn">' + esc(card.warning) + "</p>" : "");
    } else if (card.id === "risk") {
      body = '<div class="pills">' + (card.pills || []).map((p) => '<span class="pill ' + p.tone + '">' + esc(p.text) + "</span>").join("") + "</div>" +
        (card.last ? '<p class="status">' + esc(card.last) + "</p>" : "");
    } else {
      body = lines.map((line) => {
        const cls = line.strike ? "feed-row strike" : (line.verdict === "passed" ? "feed-row pass" : "feed-row");
        return '<div class="' + cls + '"><span>' + esc(line.coin) + "</span><span>" + esc(line.reason || line.verdict) + "</span></div>";
      }).join("");
    }
    const status = card.id === "scanner" && !(card.lines || []).length && !card.count
      ? "Listening for graduations"
      : (card.status || "");
    return '<article class="card snap" data-card="' + esc(card.id) + '">' +
      '<div class="card-h"><i class="status-dot" style="background:' + esc(card.color) + '"></i>' +
      "<h3>" + esc(card.name) + "</h3><b class=\"count\">" + esc(card.count) + "</b></div>" +
      '<p class="status">' + esc(status) + "</p>" +
      '<div class="body">' + body + "</div></article>";
  }

  function odometer(text) {
    const same = S.shownBalance === text;
    const animate = !same && S.shownBalance != null && !reduced();
    const prev = S.shownBalance || text;
    S.shownBalance = text;
    let html = "";
    const cols = [];
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (!/\d/.test(ch)) {
        html += '<span class="odo-char' + (ch === "$" ? " mark" : "") + '">' + esc(ch) + "</span>";
        continue;
      }
      const id = cols.length;
      cols.push(ch);
      html += '<span class="odo-col" data-digit="' + ch + '" data-i="' + id + '"><span class="odo-strip">';
      for (let d = 0; d <= 9; d++) html += "<span>" + d + "</span>";
      html += "</span></span>";
    }
    const spec = { html, animate, prev, text };
    S._odo = spec;
    return spec;
  }

  function applyOdo(root, spec) {
    root.querySelectorAll(".odo-col").forEach((col, i) => {
      const strip = col.querySelector(".odo-strip");
      const target = Number(col.dataset.digit);
      const prevCh = spec.prev[indexOfDigit(spec.prev, i)];
      const from = spec.animate && prevCh != null ? Number(prevCh) : target;
      strip.style.transform = "translateY(" + (-from) + "em)";
      if (!spec.animate) return;
      const fromRight = root.querySelectorAll(".odo-col").length - 1 - i;
      strip.style.transitionDelay = (fromRight * 30) + "ms";
      requestAnimationFrame(() => {
        strip.style.transform = "translateY(" + (-target) + "em)";
      });
    });
    const size = 68;
    root.style.fontSize = size + "px";
    const parent = root.parentElement;
    if (parent && root.scrollWidth > parent.clientWidth - 4) {
      root.style.fontSize = Math.max(46, size * (parent.clientWidth - 4) / root.scrollWidth) + "px";
    }
  }

  function indexOfDigit(text, nth) {
    let seen = 0;
    for (let i = 0; i < text.length; i++) {
      if (/\d/.test(text[i])) {
        if (seen === nth) return i;
        seen += 1;
      }
    }
    return -1;
  }

  function renderHome() {
    const state = S.state;
    const chart = (S.chart && S.chart.ranges && S.chart.ranges[S.range]) || { points: [], marks: [], empty: true };
    const emptyChart = !chart.points || chart.points.length < 2;
    const odo = odometer(state.balanceText || "—");
    const todayCls = state.todayPct < 0 ? "down" : "up";
    const agents = state.agents || [];
    const running = agents.filter((a) => !a.paused).length;
    return '<header class="nav">' +
      '<button class="avatar" data-go="account" aria-label="Account">G</button>' +
      '<button class="brand" data-go="account">Grok Bot <span class="chev">›</span></button>' +
      '<span class="grow"></span>' +
      '<button class="icon-btn" id="searchBtn" aria-label="Search">' + ICONS.search + "</button>" +
      '<button class="icon-btn bell" id="bell" aria-label="Alerts">' + ICONS.bell +
      (state.unread && !S.unreadMuted ? '<i class="dot"></i>' : "") + "</button></header>" +
      banner() +
      searchBox() +
      '<section class="balance"><div class="odo" id="odo">' + odo.html + "</div>" +
      '<div class="meta"><span class="today ' + todayCls + '">' + esc(state.todayText) + '</span>' +
      '<span class="today-label">today</span>' +
      '<span class="pill green">' + esc(state.multiplierText) + "</span>" +
      '<span class="spacer"></span>' +
      (state.mode === "PAPER" ? '<span class="pill paper">PAPER</span>' : "") +
      "</div></section>" +
      '<div class="chart-wrap"><canvas id="line"></canvas>' +
      (emptyChart ? '<div class="chart-empty">Waiting for the first trade</div>' : "") +
      "</div>" +
      '<div class="ranges">' + ["LIVE", "1H", "4H", "1D", "1W", "ALL"].map((r) =>
        '<button data-range="' + r + '" class="' + (r === S.range ? "on" : "") + '">' + r + "</button>"
      ).join("") + "</div>" +
      '<section class="section"><div class="section-head"><h2>Agents</h2><span>' + running + " running</span></div>" +
      '<div class="carousel" id="carousel">' + agents.map(homeCard).join("") + "</div></section>" +
      '<section class="section"><div class="section-head"><h2>Activity</h2>' +
      '<button class="linkish" data-go="activity">See all</button></div>' +
      activityRows(state.activity, "Nothing yet tonight") + "</section>";
  }

  function renderAgents() {
    const agents = (S.state && S.state.agents) || [];
    const blocks = agents.map((card) => {
      const locked = card.pauseLocked;
      const lines = (card.lines || []).slice(0, 20);
      const listening = card.id === "scanner" && !lines.length;
      return '<article class="agent-block" id="agent-' + esc(card.id) + '">' +
        '<header><i class="status-dot" style="background:' + esc(card.color) + '"></i>' +
        "<h3>" + esc(card.name) + "</h3><b>" + esc(card.countLabel || card.count) + "</b></header>" +
        '<p class="status">' + esc(listening ? "Listening for graduations" : card.status) + "</p>" +
        '<div class="toggle-row"><span>' + (locked ? "On while a position is open" : "Pause from Telegram") + "</span>" +
        '<span class="switch' + (card.paused ? "" : " on") + '" aria-disabled="true"><i></i></span></div>' +
        '<div class="decisions">' + (lines.length ? lines.map((line) =>
          '<div class="decision"><a href="' + esc(line.coinUrl || "#") + '" target="_blank" rel="noopener">' + esc(line.coin) + "</a>" +
          "<small>" + esc(line.time) + " · " + esc(line.reason || line.verdict) + "</small></div>"
        ).join("") : '<p class="empty-copy">Nothing yet tonight</p>') + "</div>" +
        '<button class="settings-jump" data-go="account" data-jump="settings-' + esc(card.id) + '">Settings</button>' +
        "</article>";
    }).join("");
    return nav("Agents") + banner() + '<h1 class="page-title">Agents</h1>' + blocks;
  }

  function renderActivity() {
    const rows = (S.activity && S.activity.rows) || [];
    return nav("Activity") + banner() + '<h1 class="page-title">Tonight</h1>' + activityRows(rows, "Nothing yet tonight");
  }

  function renderAccount() {
    const state = S.state;
    const handle = state.fomoHandle;
    const groups = (state.settings || []).map((group) => {
      return '<section class="group" id="settings-' + esc(group.id) + '"><h3>' + esc(group.title) + "</h3>" +
        group.rows.map((row) => {
          if (row.kind === "bool") {
            return '<div class="set-row"><b>' + esc(row.label) + '</b><span class="switch' + (Number(row.value) ? " on" : "") + '" aria-disabled="true"><i></i></span></div>';
          }
          return '<div class="set-row" style="display:block"><div style="display:flex;align-items:center"><b>' + esc(row.label) + '</b><span class="mono">' + esc(row.display) + "</span></div>" + slider(row) + "</div>";
        }).join("") +
        '<p class="locknote">Read-only here. Change it in Telegram.</p></section>';
    }).join("");
    return nav("Account") + banner() + '<h1 class="page-title">Account</h1>' +
      '<div class="segment" aria-disabled="true"><span class="' + (state.mode === "PAPER" ? "on" : "") + '">PAPER</span>' +
      '<span class="' + (state.mode === "LIVE" ? "on" : "") + '">LIVE</span></div>' +
      '<p class="hint">Change mode in Telegram. LIVE asks you to type LIVE.</p>' +
      '<div class="group"><div class="nav-row"><b>Stack</b><span class="mono">' + esc(state.stackText) + "</span></div>" +
      (handle
        ? '<a class="nav-row" href="' + esc(state.profileUrl) + '" target="_blank" rel="noopener"><b>Your FOMO profile</b><small>@' + esc(handle) + "</small></a>"
        : '<div class="nav-row"><b>Your FOMO profile</b><small>No FOMO handle yet</small></div>') +
      '<button class="nav-row" id="invite"><b>Invite a friend</b><small>Share the signup link</small></button>' +
      '<div class="nav-row"><b>Telegram</b><small>' + esc(state.telegram.detail) + "</small></div>" +
      '<div class="nav-row"><b>Send test push</b><small>Send test in Telegram</small></div>' +
      '<div class="nav-row"><b>Notifications</b><small>Telegram only. This host has no web push.</small></div></div>' +
      groups;
  }

  function renderAuditor() {
    if (!S.mint || !S.coin || !S.coin.audit) {
      return '<header class="nav"><button class="back" data-go="' + esc(S.back || "home") + '" aria-label="Back">‹</button><span class="brand">Auditor</span></header>' +
        banner() + '<p class="empty-copy">No coin taken apart yet</p>';
    }
    const coin = S.coin;
    const audit = coin.audit;
    const enabled = S.state && S.state.auditorEnabled;
    const discs = (audit.layers || []).map((layer) => {
      const cls = layer.pass === true ? "green" : layer.pass === false ? "red" : "";
      const mark = layer.pass === true ? "✓" : layer.pass === false ? "✕" : "";
      return '<div class="disc"><i class="' + cls + '">' + mark + "</i><b>" + esc(layer.name) + "</b><em>" + esc(layer.value) + "</em></div>";
    }).join("");
    return '<header class="nav"><button class="back" data-go="' + esc(S.back || "agents") + '" aria-label="Back">‹</button>' +
      '<span class="brand">' + esc(coin.symbol || coin.name) + "</span></header>" + banner() +
      '<div class="stack"><h1>' + esc(coin.name) + "</h1><p class=\"subhead\">" + esc(coin.subhead) + "</p></div>" +
      '<div class="discs">' + discs + "</div>" +
      '<div class="verdict"><strong>' + esc(audit.headline) + "</strong><p>" + esc(audit.sub) + "</p></div>" +
      '<div class="toggle-row"><span>Take apart before buying</span><span class="switch' + (enabled ? " on" : "") + '" aria-disabled="true"><i></i></span></div>' +
      '<p class="hint">Read-only here. Change it in Telegram.</p>' +
      '<div class="fomo"><a class="btn" href="' + esc(coin.coinUrl) + '" target="_blank" rel="noopener">Open on FOMO</a></div>';
  }

  function renderCoin() {
    if (!S.mint || !S.coin) {
      return '<header class="nav"><button class="back" data-go="' + esc(S.back || "home") + '" aria-label="Back">‹</button><span class="brand">Coin</span></header>' +
        banner() + '<p class="empty-copy">No coin yet</p>';
    }
    const coin = S.coin;
    const pos = coin.position;
    const sinceCls = (coin.sinceEntry || 0) < 0 ? "down" : "up";
    const stats = [
      ["Market cap", coin.marketCapText],
      ["Liquidity", coin.liquidityText],
      ["24h volume", coin.volumeText],
      ["Holders", coin.holdersText],
    ];
    return '<header class="nav"><button class="back" data-go="' + esc(S.back || "home") + '" aria-label="Back">‹</button>' +
      '<span class="brand">' + esc(coin.symbol || "Coin") + "</span><span class=\"grow\"></span>" +
      (coin.mode === "PAPER" ? '<span class="pill paper">PAPER</span>' : "") +
      (coin.shield ? '<span class="pill shield">' + esc(coin.shield) + "</span>" : "") +
      "</header>" + banner() +
      '<div class="stack"><h1>' + esc(coin.name) + '</h1><p class="subhead">' + esc(coin.subhead) + "</p>" +
      '<div class="price">' + esc(coin.priceText) + "</div>" +
      (coin.sinceText ? '<p class="since ' + sinceCls + '">' + esc(coin.sinceText) + "</p>" : "") +
      "</div>" +
      '<div class="candle-wrap"><canvas id="candles"></canvas></div>' +
      '<div class="stats">' + stats.map((s) => "<div><small>" + s[0] + "</small><b>" + esc(s[1]) + "</b></div>").join("") + "</div>" +
      (pos ? '<div class="panel"><div class="kicker">Your position · ' + esc(pos.sizeText) + " in · Executor</div>" +
        '<div class="exit-line"><span>Stop</span><b>' + esc(pos.stopText) + "</b></div>" +
        '<div class="exit-line"><span>Trail</span><b>' + esc(pos.trailText) + "</b></div>" +
        '<div class="exit-line"><span>Time left</span><b>' + esc(pos.timeoutText) + "</b></div></div>" : "") +
      '<div class="fomo"><a class="btn" href="' + esc(coin.coinUrl) + '" target="_blank" rel="noopener">' +
      (pos ? "Sell on FOMO" : "Buy on FOMO") + "</a></div>";
  }

  function renderLoading() {
    return '<header class="nav"><span class="avatar"></span><span class="brand">Grok Bot</span></header>' +
      '<div class="balance"><div class="skel" style="height:68px;width:70%"></div>' +
      '<div class="skel" style="height:22px;width:40%;margin-top:12px"></div></div>' +
      '<div class="skel" style="height:212px;margin:16px"></div>' +
      '<div class="skel" style="height:280px;margin:16px;border-radius:30px"></div>';
  }

  function renderGate() {
    return '<div class="gate"><div><h1>Grok Bot</h1><p>Open the desk link once. It ends with #d= and this phone keeps it.</p></div></div>';
  }

  function nav(title) {
    return '<header class="nav"><button class="avatar" data-go="account" aria-label="Account">G</button>' +
      '<button class="brand" data-go="home">' + esc(title) + ' <span class="chev">›</span></button></header>';
  }

  function banner() {
    const bad = S.fetchError || (S.state && S.state.offline);
    if (!bad) return "";
    return '<div class="offline" data-retry>' + esc(offlineText()) + "</div>";
  }

  function searchBox() {
    if (!S.searching) return "";
    const q = S.query.trim().toLowerCase();
    const coins = ((S.state && S.state.coins) || []).filter((c) => {
      if (!q) return true;
      return (c.name || "").toLowerCase().includes(q) || (c.symbol || "").toLowerCase().includes(q) || (c.mint || "").toLowerCase().includes(q);
    });
    return '<div class="search"><input id="q" placeholder="Name, symbol, or mint" value="' + esc(S.query) + '">' +
      coins.map((c) => '<button class="result" data-go="coin" data-mint="' + esc(c.mint) + '"><b>' + esc(c.symbol || c.name) + "</b><small>" + esc(c.priceText) + "</small></button>").join("") +
      (q && !coins.length ? '<p class="empty-copy">No match</p>' : "") + "</div>";
  }

  function tabs() {
    const items = [
      ["home", "Home"],
      ["agents", "Agents"],
      ["activity", "Activity"],
      ["account", "Account"],
    ];
    return '<nav class="tabs">' + items.map(([id, label]) =>
      '<button data-go="' + id + '" class="' + (S.screen === id ? "on" : "") + '">' + ICONS[id] + "<span>" + label + "</span></button>"
    ).join("") + "</nav>";
  }

  function render() {
    const y = window.scrollY;
    const app = document.getElementById("app");
    const pushed = S.screen === "coin" || S.screen === "auditor";
    document.body.classList.toggle("pushed", pushed);
    let html;
    if (!S.dataPath) html = renderGate();
    else if (S.loading && !S.state) html = renderLoading();
    else if (!S.state) html = nav("Grok Bot") + banner() + '<p class="empty-copy">Waiting for the first trade</p>';
    else if (S.screen === "home") html = renderHome();
    else if (S.screen === "agents") html = renderAgents();
    else if (S.screen === "activity") html = renderActivity();
    else if (S.screen === "account") html = renderAccount();
    else if (S.screen === "auditor") html = renderAuditor();
    else html = renderCoin();
    app.innerHTML = html + (S.dataPath ? tabs() : "") + '<div class="toast" id="toast"></div>';
    bind();
    if (S.screen === "home" && S.state) {
      const odo = document.getElementById("odo");
      if (odo && S._odo) applyOdo(odo, S._odo);
      const canvas = document.getElementById("line");
      const chart = S.chart && S.chart.ranges && S.chart.ranges[S.range];
      if (canvas && chart && window.DeskChart) {
        window.DeskChart.drawLine(canvas, chart.points, chart.marks, S.chart.startValue);
      }
      sizeCards();
    }
    if (S.screen === "coin" && S.coin && window.DeskChart) {
      const canvas = document.getElementById("candles");
      if (canvas) window.DeskChart.drawCandles(canvas, S.coin.candles, S.coin.exitPrice);
    }
    if (S.searching) {
      const q = document.getElementById("q");
      if (q) {
        q.focus();
        q.setSelectionRange(q.value.length, q.value.length);
      }
    } else {
      window.scrollTo(0, y);
    }
    document.documentElement.dataset.ready = "1";
  }

  function sizeCards() {
    document.querySelectorAll(".card.snap").forEach((card) => {
      card.style.borderRadius = (42 * card.offsetWidth / 504) + "px";
    });
  }

  function bind() {
    document.querySelectorAll("[data-go]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (node.tagName === "A") return;
        const screen = node.getAttribute("data-go");
        const mint = node.hasAttribute("data-mint") ? node.getAttribute("data-mint") : undefined;
        const jump = node.getAttribute("data-jump");
        if (screen === "account" && node.id === "bell") return;
        go(screen, mint);
        if (jump) {
          const el = document.getElementById(jump);
          if (el) el.scrollIntoView({ block: "start" });
        }
      });
    });
    document.querySelectorAll("[data-range]").forEach((node) => {
      node.addEventListener("click", () => {
        S.range = node.getAttribute("data-range");
        render();
      });
    });
    const search = document.getElementById("searchBtn");
    if (search) search.addEventListener("click", () => { S.searching = !S.searching; S.query = ""; render(); });
    const bell = document.getElementById("bell");
    if (bell) bell.addEventListener("click", () => { S.unreadMuted = true; go("activity"); });
    const q = document.getElementById("q");
    if (q) q.addEventListener("input", () => { S.query = q.value; S.searching = true; render(); });
    const invite = document.getElementById("invite");
    if (invite) invite.addEventListener("click", shareInvite);
  }

  async function shareInvite() {
    const url = S.state && S.state.signupUrl;
    if (!url) return;
    if (navigator.share) {
      try {
        await navigator.share({ title: "FOMO", url });
        return;
      } catch (err) {
        if (err && err.name === "AbortError") return;
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied");
    } catch (err) {
      toast("Link copied");
    }
  }

  function toast(text) {
    const node = document.getElementById("toast");
    if (!node) return;
    node.textContent = text;
    node.classList.add("show");
    setTimeout(() => node.classList.remove("show"), 1600);
  }

  function boot() {
    readPath();
    if ("serviceWorker" in navigator && !document.documentElement.classList.contains("shot")) {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    }
    poll();
    setInterval(poll, 5000);
    setInterval(tick, 1000);
    window.addEventListener("resize", () => {
      if (S.screen === "home" || S.screen === "coin") render();
    });
  }

  boot();
})();
