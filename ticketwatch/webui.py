"""The control panel page. Served from memory so the .exe needs no data files."""

from __future__ import annotations

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ticketwatch</title>
<style>
  :root {
    --bg: #f4f6f9; --card: #ffffff; --ink: #0f172a; --muted: #64748b; --line: #e2e8f0;
    --accent: #0b8a3d; --accent-soft: #e7f6ec; --warn: #b45309; --bad: #b91c1c;
    --chip: #f1f5f9; --shadow: 0 1px 3px rgba(15,23,42,.08);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0b1120; --card: #151d2e; --ink: #e8edf7; --muted: #94a3b8; --line: #263248;
      --accent: #34d17f; --accent-soft: #13301f; --warn: #fbbf24; --bad: #f87171;
      --chip: #1e293b; --shadow: none;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 980px; margin: 0 auto; padding: 20px 16px 60px; }
  h1 { font-size: 20px; margin: 0; letter-spacing: -.01em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 26px 0 10px; }
  a { color: inherit; }

  header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
  .sub { color: var(--muted); font-size: 13px; margin-top: 2px; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; }

  button {
    font: inherit; font-weight: 600; cursor: pointer; border-radius: 8px; padding: 9px 16px;
    border: 1px solid var(--line); background: var(--card); color: var(--ink);
  }
  button:hover { border-color: var(--muted); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.primary:hover { filter: brightness(1.08); }
  button:disabled { opacity: .5; cursor: default; }

  .pill { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600;
          padding: 5px 12px; border-radius: 999px; background: var(--chip); color: var(--muted); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .pill.on { background: var(--accent-soft); color: var(--accent); }
  .pill.on .dot { background: var(--accent); animation: pulse 2s infinite; }
  .pill.bad { color: var(--bad); }
  .pill.bad .dot { background: var(--bad); }
  @keyframes pulse { 50% { opacity: .35; } }

  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 16px; box-shadow: var(--shadow); }
  .hero { border-left: 4px solid var(--accent); background: var(--accent-soft); margin-top: 18px; }
  .hero .big { font-size: 22px; font-weight: 700; margin: 2px 0 6px; }
  .hero .buy { display: inline-block; margin-top: 10px; background: var(--accent); color: #fff;
               text-decoration: none; font-weight: 700; padding: 11px 22px; border-radius: 8px; }

  .strip { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
  .plat { display: flex; align-items: center; gap: 7px; font-size: 13px; padding: 6px 12px;
          border-radius: 999px; background: var(--chip); border: 1px solid transparent; }
  .plat.off { opacity: .55; }
  .plat.err { border-color: var(--bad); color: var(--bad); }

  .show { margin-bottom: 12px; }
  .show .top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: baseline; }
  .show .date { font-weight: 700; font-size: 16px; }
  .show .venue { color: var(--muted); font-size: 13px; }
  .badge { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
           padding: 3px 9px; border-radius: 6px; background: var(--chip); color: var(--muted); }
  .badge.go { background: var(--accent-soft); color: var(--accent); }
  .badge.no { color: var(--bad); }
  .badge.wait { color: var(--warn); }

  table.quotes { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }
  table.quotes td { padding: 7px 0; border-top: 1px solid var(--line); }
  table.quotes td.price { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
  table.quotes tr.best td { color: var(--accent); }
  table.quotes td.go { text-align: right; width: 1%; white-space: nowrap; padding-left: 14px; }
  table.quotes a { color: var(--accent); text-decoration: none; font-weight: 600; }
  .elsewhere { margin-top: 10px; font-size: 13px; color: var(--muted); display: flex; gap: 10px; flex-wrap: wrap; }
  .elsewhere a { color: var(--muted); }

  .feed div { padding: 8px 0; border-top: 1px solid var(--line); font-size: 13px; }
  .feed div:first-child { border-top: 0; }
  .feed .when { color: var(--muted); font-size: 12px; }
  .feed .warn { color: var(--warn); } .feed .error { color: var(--bad); }
  .feed b.go { color: var(--accent); }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px; }
  input[type=text], input[type=number], input[type=password] {
    width: 100%; font: inherit; padding: 8px 10px; border-radius: 8px;
    border: 1px solid var(--line); background: var(--bg); color: var(--ink);
  }
  .check { display: flex; align-items: center; gap: 8px; font-size: 14px; margin-top: 8px; }
  .check input { width: auto; }
  details summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--muted);
                    text-transform: uppercase; letter-spacing: .08em; margin: 26px 0 10px; }
  .hint { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .err { color: var(--bad); font-size: 13px; margin-top: 10px; }
  .empty { color: var(--muted); font-size: 14px; }
  @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>ticketwatch</h1>
      <div class="sub" id="sub">loading...</div>
    </div>
    <div class="controls">
      <span class="pill" id="status"><span class="dot"></span><span id="statusText">...</span></span>
      <button id="checkBtn">Check now</button>
      <button id="runBtn" class="primary">Start watching</button>
    </div>
  </header>

  <div id="error" class="err" hidden></div>
  <div id="hero" hidden></div>
  <div class="strip" id="platforms"></div>

  <h2>Shows found <span id="count"></span></h2>
  <div id="shows"><div class="card empty">Nothing yet. Press <b>Check now</b>.</div></div>

  <div class="grid">
    <div>
      <h2>Alerts</h2>
      <div class="card feed" id="alerts"><div class="empty">No alerts yet.</div></div>
    </div>
    <div>
      <h2>Activity</h2>
      <div class="card feed" id="log"><div class="empty">Nothing logged yet.</div></div>
    </div>
  </div>

  <details id="settings">
    <summary>Settings</summary>
    <div class="card">
      <div class="grid">
        <div><label for="keyword">Artist</label><input type="text" id="keyword"></div>
        <div><label for="cities">Cities (comma separated)</label><input type="text" id="cities"></div>
        <div><label for="interval">Check every (seconds)</label><input type="number" id="interval" min="5"></div>
        <div><label for="drop">Tell me about price drops over (%)</label><input type="number" id="drop" min="0" step="1"></div>
      </div>

      <h2>Platforms</h2>
      <div class="grid">
        <div>
          <label for="apiKey">Ticketmaster API key</label>
          <input type="password" id="apiKey" placeholder="required">
          <div class="hint">Free from developer.ticketmaster.com</div>
        </div>
        <div>
          <label for="seatgeek">SeatGeek client ID</label>
          <input type="password" id="seatgeek" placeholder="optional - adds price comparison">
          <div class="hint">seatgeek.com/account/develop</div>
        </div>
        <div>
          <label for="bandsintown">Bandsintown app id</label>
          <input type="text" id="bandsintown" placeholder="optional - finds new dates early">
          <div class="hint">Any app name you choose. Clear it to switch Bandsintown off.</div>
        </div>
      </div>

      <h2>Alerts</h2>
      <div class="grid">
        <div>
          <label for="email">Email alerts to</label>
          <input type="text" id="email" placeholder="you@example.com">
        </div>
        <div>
          <label for="emailPass">Email app password</label>
          <input type="password" id="emailPass" placeholder="leave blank to keep current">
          <div class="hint" id="emailHint"></div>
        </div>
        <div><label for="ntfy">Phone push (ntfy topic)</label><input type="text" id="ntfy" placeholder="optional"></div>
        <div>
          <div class="check"><input type="checkbox" id="desktop"><label for="desktop" style="margin:0">Desktop popups</label></div>
          <div class="check"><input type="checkbox" id="openBrowser"><label for="openBrowser" style="margin:0">Open the buy page automatically</label></div>
        </div>
      </div>

      <div class="controls" style="margin-top:16px">
        <button class="primary" id="saveBtn">Save settings</button>
        <button id="testBtn">Send test alert</button>
      </div>
      <div class="hint" id="configPath"></div>
    </div>
  </details>
</div>

<script>
const TOKEN = "__TOKEN__";
const q = TOKEN ? "?token=" + encodeURIComponent(TOKEN) : "";
const $ = (id) => document.getElementById(id);
let touched = false;   // do not overwrite settings fields while they are being edited

async function api(path, body) {
  const res = await fetch(path + q, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function ago(iso) {
  if (!iso) return "";
  const secs = Math.round((Date.now() - new Date(iso)) / 1000);
  if (secs < 60) return secs + "s ago";
  if (secs < 3600) return Math.round(secs / 60) + "m ago";
  return Math.round(secs / 3600) + "h ago";
}
function until(iso) {
  if (!iso) return "";
  const secs = Math.round((new Date(iso) - Date.now()) / 1000);
  if (secs <= 0) return "any moment";
  if (secs < 60) return "in " + secs + "s";
  if (secs < 86400) return "in " + Math.round(secs / 60) + "m";
  return "in " + Math.round(secs / 86400) + "d";
}

function badgeClass(state) {
  if (state === "on_sale" || state === "few_left" || state === "presale") return "go";
  if (state === "sold_out" || state === "cancelled") return "no";
  return "wait";
}

function render(state) {
  if (!state) return;
  const s = state.settings || {};
  $("sub").textContent = s.keyword + " in " + (s.cities || []).join(", ") +
    " - every " + s.interval_seconds + "s" +
    (state.last_check ? " - last checked " + ago(state.last_check) : "");

  const pill = $("status");
  pill.className = "pill " + (state.error ? "bad" : state.running ? "on" : "");
  $("statusText").textContent = state.error ? "Problem" : state.running
    ? (state.next_check ? "Watching - next check " + until(state.next_check) : "Watching")
    : "Stopped";
  $("runBtn").textContent = state.running ? "Stop" : "Start watching";
  $("error").hidden = !state.error;
  $("error").textContent = state.error || "";

  // cheapest buyable right now
  const hero = $("hero");
  if (state.cheapest) {
    const c = state.cheapest;
    hero.hidden = false;
    hero.className = "card hero";
    hero.innerHTML =
      '<div class="badge go">Available now</div>' +
      '<div class="big">' + esc(c.price || "On sale") + "</div>" +
      "<div>" + esc(c.name) + " - " + esc(c.when) + " - " + esc(c.where) + "</div>" +
      '<a class="buy" target="_blank" rel="noopener" href="' + esc(c.url) + '">Buy on ' +
      esc(c.cheapest_platform || "Ticketmaster") + "</a>";
  } else if (state.buyable_count > 0) {
    hero.hidden = false;
    hero.className = "card hero";
    hero.innerHTML = '<div class="badge go">Available now</div><div class="big">' +
      state.buyable_count + " show(s) on sale</div>";
  } else {
    hero.hidden = true;
  }

  $("platforms").innerHTML = (state.platforms || []).map(p =>
    '<span class="plat ' + (p.error ? "err" : p.configured ? "" : "off") + '" title="' + esc(p.error || p.hint) + '">' +
    (p.error ? "&#9888;" : p.configured ? "&#10003;" : "&#8213;") + " " + esc(p.label) +
    (p.configured ? "" : " (not set up)") + "</span>").join("");

  $("count").textContent = state.events.length ? "(" + state.events.length + ")" : "";
  $("shows").innerHTML = state.events.length ? state.events.map(showCard).join("") :
    '<div class="card empty">No matching shows yet. New dates will appear here the moment any platform lists one.</div>';

  $("alerts").innerHTML = (state.alerts || []).length ? state.alerts.map(a =>
    "<div><b class='" + (a.urgent ? "go" : "") + "'>" + esc(a.title) + "</b>" +
    (a.url ? ' <a target="_blank" rel="noopener" href="' + esc(a.url) + '">open</a>' : "") +
    '<div class="when">' + ago(a.at) + "</div></div>").join("") : '<div class="empty">No alerts yet.</div>';

  $("log").innerHTML = (state.log || []).length ? state.log.map(l =>
    '<div class="' + esc(l.level) + '">' + esc(l.text) + '<div class="when">' + ago(l.at) + "</div></div>").join("")
    : '<div class="empty">Nothing logged yet.</div>';

  $("configPath").textContent = "Saved to " + state.config_path + " - readable only by you.";
  $("emailHint").textContent = s.email_password_set ? "A password is already saved." :
    "Gmail needs an app password (myaccount.google.com/apppasswords).";

  if (!touched) {
    $("keyword").value = s.keyword || "";
    $("cities").value = (s.cities || []).join(", ");
    $("interval").value = s.interval_seconds;
    $("drop").value = s.price_drop_percent;
    $("apiKey").placeholder = s.api_key_set ? "saved - type to replace" : "required";
    $("seatgeek").placeholder = s.seatgeek_client_id_set ? "saved - type to replace" : "optional - adds price comparison";
    $("bandsintown").value = s.bandsintown_app_id || "";
    $("email").value = s.email_to || "";
    $("ntfy").value = s.ntfy_topic || "";
    $("desktop").checked = !!s.desktop;
    $("openBrowser").checked = !!s.open_browser;
  }
}

function showCard(e) {
  const quotes = (e.quotes || []).map((quote, i) =>
    "<tr class='" + (i === 0 ? "best" : "") + "'><td>" + esc(quote.platform) +
    (quote.count ? ' <span class="venue">' + quote.count + " listings</span>" : "") +
    '</td><td class="price">' + esc(quote.price || "-") + "</td>" +
    '<td class="go">' + (quote.url ? '<a target="_blank" rel="noopener" href="' + esc(quote.url) + '">buy</a>' : "") +
    "</td></tr>").join("");

  const searches = (e.links || []).filter(l => !l.price).map(l =>
    '<a target="_blank" rel="noopener" href="' + esc(l.url) + '">' + esc(l.label) + " &#8599;</a>").join("");

  return '<div class="card show">' +
    '<div class="top"><div><div class="date">' + esc(e.when) + "</div>" +
    '<div class="venue">' + esc(e.where) + "</div></div>" +
    '<div><span class="badge ' + badgeClass(e.availability) + '">' + esc(e.availability.replace(/_/g, " ")) + "</span>" +
    (e.on_sale_at ? ' <span class="venue">on sale ' + until(e.on_sale_at) + "</span>" : "") + "</div></div>" +
    (quotes ? '<table class="quotes">' + quotes + "</table>" :
      '<div class="venue" style="margin-top:10px">No prices published yet' +
      (e.platforms.length ? " (seen on " + esc(e.platforms.join(", ")) + ")" : "") + "</div>") +
    (e.mixed_currency ? '<div class="hint">Prices are in different currencies - compare carefully.</div>' : "") +
    (searches ? '<div class="elsewhere">Also check: ' + searches + "</div>" : "") +
    "</div>";
}

async function act(path, body) { render(await api(path, body || {})); }

$("runBtn").onclick = async () => {
  const running = $("runBtn").textContent === "Stop";
  $("runBtn").disabled = true;
  await act(running ? "/api/stop" : "/api/start");
  $("runBtn").disabled = false;
};
$("checkBtn").onclick = async () => {
  $("checkBtn").disabled = true; $("checkBtn").textContent = "Checking...";
  await act("/api/check");
  $("checkBtn").disabled = false; $("checkBtn").textContent = "Check now";
};
$("testBtn").onclick = () => act("/api/test-notify");
$("saveBtn").onclick = async () => {
  const patch = {
    keyword: $("keyword").value.trim(),
    cities: $("cities").value.split(",").map(c => c.trim()).filter(Boolean),
    interval_seconds: parseInt($("interval").value, 10) || 60,
    price_drop_percent: parseFloat($("drop").value) || 0,
    bandsintown_app_id: $("bandsintown").value.trim(),
    notifiers: {
      email_to: $("email").value.trim(),
      ntfy_topic: $("ntfy").value.trim(),
      desktop: $("desktop").checked,
      open_browser: $("openBrowser").checked,
    },
  };
  if ($("apiKey").value.trim()) patch.api_key = $("apiKey").value.trim();
  if ($("seatgeek").value.trim()) patch.seatgeek_client_id = $("seatgeek").value.trim();
  if ($("emailPass").value.trim()) patch.notifiers.smtp_password = $("emailPass").value.trim();

  $("saveBtn").disabled = true;
  touched = false;
  await act("/api/settings", patch);
  $("apiKey").value = ""; $("seatgeek").value = ""; $("emailPass").value = "";
  $("saveBtn").disabled = false;
};
document.querySelectorAll("#settings input").forEach(el => {
  el.addEventListener("input", () => { touched = true; });
});

async function poll() { try { render(await api("/api/state")); } catch (e) {} }
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""
