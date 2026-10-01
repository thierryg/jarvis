/* =============================================================================
 * Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
 * -----------------------------------------------------------------------------
 * File    : jarvis/web/static/app.js
 * Purpose : Web console logic (i18n, navigation, pages, API client)
 * Author  : Thierry Gayet <thierry.gayet@labworks.fr>
 * Project : jarvis-home (version: jarvis/VERSION)
 * Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
 * =============================================================================
 * Framework-free single-page application.
 *  - i18n: texts come from i18n/<locale>.json (en-US is the reference and the fallback). The
 *    locale is the user's choice (localStorage) or the server default (Settings > Web UI).
 *  - Security: every state-changing request carries the X-Jarvis anti-CSRF header; background
 *    polling carries X-Jarvis-Background so that it does not extend the idle session.
 *  - CSP: no inline style attributes are generated (element.style is used instead).
 *
 * Architecture overview
 * ---------------------
 *  - Loading: index.html loads this file once, at the end of <body>. The CSP set by the
 *    nginx reverse proxy is `default-src 'self'` (no 'unsafe-inline', no 'unsafe-eval'), so
 *    there is no inline script, no eval, and no style="" attribute. All DOM lookups at
 *    module level therefore run against a fully parsed document.
 *  - i18n: loadLocale() always fetches en-US first (kept in `fallback`), then the selected
 *    locale (kept in `dict`). t() resolves a key as dict -> fallback -> caller default -> key,
 *    so a missing or partial translation file degrades to English, never to a blank label.
 *    Static markup is translated through its data-i18n / data-i18n-placeholder attributes by
 *    applyI18n(); dynamic markup calls t() directly while it is built.
 *    Locale precedence at boot: localStorage "jarvis.lang" > server default (GET /api/ui,
 *    setting ui.language) > navigator.language > en-US.
 *  - API client: api() wraps fetch() for every JSON endpoint of jarvis/web/app.py. The
 *    session is an HttpOnly, SameSite=Strict cookie (never visible to this script); every
 *    request sends `X-Jarvis: 1` (required by the server on all non-GET/HEAD requests as an
 *    anti-CSRF check), and auto-refresh requests additionally send `X-Jarvis-Background: 1`
 *    so they do not count as user activity (an unattended tab still reaches the idle timeout).
 *  - Session expiry: any 401 (except on /api/login itself) switches back to the login view
 *    with an "expired" or "expired (idle)" notice and throws Error("session"); guard() swallows
 *    that sentinel so no error toast is shown on top of the login screen.
 *  - Pages: the sidebar buttons carry data-page="<name>"; go() shows <div id="page-<name>">,
 *    hides the others and runs the matching loader from PAGES. Leaving the Live page drops the
 *    MJPEG <img> source (closes the long-lived stream); leaving Recordings stops the time-lapse.
 *  - Polling: only GET /api/status is polled (every 5 s, as a background request) to refresh
 *    the top-bar chips and, on the Live page, the KPI tiles. Everything else is loaded on
 *    demand (page change, filter submit, "Load older" keyset pagination with before_id).
 *  - Media: stored images are never linked directly; they go through GET /api/media?path=...
 *    which checks the session and only serves files under the data directory's faces/,
 *    unknown/, sightings/, recordings/ and timelapse/ folders (404 otherwise).
 *  - Errors: event handlers are wrapped in guard(), which turns thrown errors (including the
 *    server's `detail` message) into red toasts.
 */
"use strict";

// ----------------------------------------------------------------------------- i18n
/**
 * Supported UI locales (BCP 47 code -> native language name). Also used to populate every
 * `.lang-select` and to label the choices of the `ui.language` setting.
 * @type {Object<string, string>}
 */
const LANGS = {
  "en-US": "English (US)", "fr-FR": "Français", "es-ES": "Español", "nl-NL": "Nederlands", "de-DE": "Deutsch",
  "it-IT": "Italiano", "ru-RU": "Русский", "zh-CN": "中文（简体）", "id-ID": "Bahasa Indonesia", "ko-KR": "한국어",
  "ja-JP": "日本語", "th-TH": "ไทย",
};
/** Active locale code (always a key of LANGS). @type {string} */
let locale = "en-US";
/** Translation table of the active locale (same object as `fallback` for en-US). @type {Object<string, string>} */
let dict = {};
/** en-US reference table, loaded once and used for every missing key. @type {Object<string, string>} */
let fallback = {};

/**
 * Loads and activates a UI locale, then re-translates the whole document.
 *
 * The en-US reference file is fetched once and cached in `fallback`. An unknown code falls
 * back to en-US; a translation file that fails to load or parse yields an empty table, so
 * every key silently resolves to English through t().
 * Static files fetched: GET i18n/en-US.json (first call only) and GET i18n/<code>.json.
 * `cache: "no-cache"` forces revalidation so an updated translation is picked up on reload.
 *
 * @param {string} code - Requested locale code (e.g. "fr-FR"); may be unsupported.
 * @returns {Promise<void>} Resolves once the texts and the language selectors are updated.
 * @throws {Error} Only if the en-US reference file itself cannot be fetched or parsed.
 */
async function loadLocale(code) {
  // A missing or unreachable table (file:// page, network error) never aborts the boot: the
  // English texts written in index.html stay as the defaults (see applyI18n()).
  const get = async (c) => {
    try {
      const r = await fetch(`i18n/${c}.json`, { cache: "no-cache" });
      return r.ok ? await r.json() : {};
    } catch { return {}; }
  };
  if (!Object.keys(fallback).length) fallback = await get("en-US");
  locale = LANGS[code] ? code : "en-US";
  dict = locale === "en-US" ? fallback : await get(locale);
  document.documentElement.lang = locale;
  applyI18n(document);
  for (const sel of document.querySelectorAll(".lang-select")) sel.value = locale;
}

/**
 * Translates a key; {name} placeholders are replaced from vars. Falls back to en-US, then to `def`.
 *
 * @param {string} key - Dotted translation key (e.g. "nav.live").
 * @param {Object<string, (string|number)>} [vars={}] - Placeholder values, `{k}` -> `vars.k`.
 * @param {string} [def] - Default text when the key exists in neither table (the key itself
 *   is returned when `def` is also undefined, which makes missing keys visible).
 * @returns {string} The translated, interpolated text.
 */
function t(key, vars = {}, def = undefined) {
  let s = dict[key] ?? fallback[key] ?? def ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

/**
 * Translates the static markup below `root`: `data-i18n` sets textContent and
 * `data-i18n-placeholder` sets the placeholder. The current text/placeholder authored in
 * index.html (English) is used as the default, so an unknown key keeps the original wording.
 * Also refreshes the top-bar page title.
 *
 * @param {ParentNode} root - Subtree to translate (usually `document`).
 * @returns {void}
 */
function applyI18n(root) {
  for (const n of root.querySelectorAll("[data-i18n]")) n.textContent = t(n.dataset.i18n, {}, n.textContent);
  for (const n of root.querySelectorAll("[data-i18n-placeholder]")) n.placeholder = t(n.dataset.i18nPlaceholder, {}, n.placeholder);
  for (const n of root.querySelectorAll("[data-i18n-alt]")) n.alt = t(n.dataset.i18nAlt, {}, n.alt);
  if (currentPage) $("#page-title").textContent = t(`nav.${currentPage}`);
}

/**
 * Formats a Unix timestamp (seconds) as a localized date and time; "—" for a missing value.
 * @param {?number} ts - Seconds since the epoch (server timestamps are floats in seconds).
 * @returns {string}
 */
const fmtDateTime = (ts) => ts ? new Date(ts * 1000).toLocaleString(locale) : "—";
/**
 * Formats a Unix timestamp (seconds) as the local calendar day "YYYY-MM-DD" expected by
 * <input type="date"> (unlike toISOString(), which gives the UTC day).
 * @param {number} ts - Seconds since the epoch.
 * @returns {string}
 */
const localDateInput = (ts) => {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
/**
 * Formats a Unix timestamp (seconds) as a localized date only.
 * @param {number} ts - Seconds since the epoch.
 * @returns {string}
 */
const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString(locale);
/**
 * Formats a Unix timestamp (seconds) as a localized time only.
 * @param {number} ts - Seconds since the epoch.
 * @returns {string}
 */
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString(locale);
/**
 * Formats a duration in seconds: whole minutes (at least 1) below one hour, else hours with
 * one decimal, using the translated "unit.minutes" / "unit.hours" templates.
 * @param {number} s - Duration in seconds.
 * @returns {string}
 */
const fmtDuration = (s) => s < 3600 ? t("unit.minutes", { n: Math.max(1, Math.round(s / 60)) })
                                    : t("unit.hours", { n: (s / 3600).toFixed(1) });

// ----------------------------------------------------------------------------- DOM helpers
/**
 * Shorthand for `root.querySelector(sel)`.
 * @param {string} sel - CSS selector.
 * @param {ParentNode} [root=document] - Search scope.
 * @returns {?Element} The first match, or null.
 */
const $ = (sel, root = document) => root.querySelector(sel);

/**
 * Minimal hyperscript helper that builds a DOM element.
 *
 * Attribute handling:
 *  - `on<event>` keys register event listeners (e.g. `onclick`, `onchange`);
 *  - `style` must be an object and is applied through the CSSOM (`node.style`), which the
 *    CSP allows, unlike a `style="..."` attribute (blocked: no 'unsafe-inline' for styles);
 *  - `true` creates an empty boolean attribute; `false`, null and undefined are skipped;
 *  - anything else is set with setAttribute().
 * Children may be nested arrays; strings/numbers become text nodes (never parsed as HTML,
 * so server data cannot inject markup), null/false are skipped.
 *
 * @param {string} tag - Element name.
 * @param {Object<string, *>} [attrs={}] - Attributes, listeners and style object.
 * @param {...(Node|string|number|null|false|Array)} children - Child content.
 * @returns {HTMLElement} The new element.
 */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(node.style, v);           // CSSOM: allowed by the CSP
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c instanceof Node ? c : document.createTextNode(c));
  return node;
}

/**
 * Creates an inline SVG icon that references a <symbol id="i-NAME"> of the sprite in
 * index.html (SVG elements need the SVG namespace, hence createElementNS).
 * @param {string} name - Icon name without the "i-" prefix (e.g. "play", "trash").
 * @returns {SVGSVGElement}
 */
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

/**
 * Shows a transient notification in the bottom-right corner (#toasts, an aria-live region);
 * it removes itself after 4.5 s.
 * @param {string} msg - Text to display (inserted as text, not HTML).
 * @param {boolean} [isError=false] - Red styling for errors.
 * @returns {void}
 */
function toast(msg, isError = false) {
  const n = el("div", { class: `toast ${isError ? "error" : ""}` }, msg);
  $("#toasts").append(n);
  setTimeout(() => n.remove(), 4500);
}

/**
 * Wraps a (possibly async) handler so that any error becomes an error toast instead of an
 * unhandled rejection. The Error("session") sentinel thrown by api() on a 401 is swallowed:
 * the login screen is already displayed with its own notice.
 * @param {function(...*): (*|Promise<*>)} fn - Handler to protect.
 * @returns {function(...*): Promise<void>} The wrapped handler (never rejects).
 */
const guard = (fn) => async (...args) => {
  try { await fn(...args); } catch (e) { if (e.message !== "session") toast(e.message, true); }
};

/**
 * Builds a colored pill.
 * @param {string} text - Label.
 * @param {string} [kind=""] - Color variant: "ok", "bad", "warn", "info" or "" (neutral).
 * @returns {HTMLSpanElement}
 */
function badge(text, kind = "") { return el("span", { class: `badge ${kind}` }, text); }

// ----------------------------------------------------------------------------- API client
/**
 * Calls a JSON endpoint of the Jarvis API (same origin, session cookie).
 *
 * Every request carries `X-Jarvis: 1`: the server rejects state-changing requests without it
 * (403), which a cross-site form or image cannot add (anti-CSRF). With `background: true` it
 * also sends `X-Jarvis-Background: 1`, so the server authenticates the request without
 * refreshing the session's last-activity time (auto-refresh must not keep an idle tab alive).
 *
 * On 401 (except for POST /api/login, whose 401 means "bad credentials") the session is gone:
 * the login view is shown with an idle-timeout or generic expiry notice, chosen from the
 * server's `detail` text ("Session expired (idle timeout)" vs "Session expired").
 *
 * @param {string} path - Absolute API path including the query string (e.g. "/api/events?limit=100").
 * @param {Object} [options]
 * @param {string} [options.method="GET"] - HTTP method.
 * @param {*} [options.json] - Body serialized as JSON (sets Content-Type: application/json).
 * @param {FormData} [options.form] - Multipart body (file uploads); ignored when `json` is given.
 * @param {boolean} [options.background=false] - Mark as background polling (does not extend the session).
 * @returns {Promise<?*>} The parsed JSON body, or null when the response is not JSON.
 * @throws {Error} "session" after a 401 (login view already shown); otherwise the server's
 *   `detail` (string, or JSON-encoded validation errors) or "HTTP <status>" on any non-2xx.
 */
async function api(path, { method = "GET", json, form, background = false } = {}) {
  const headers = { "X-Jarvis": "1" };
  if (background) headers["X-Jarvis-Background"] = "1";
  const opts = { method, headers, credentials: "same-origin" };
  if (json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(json); }
  else if (form) opts.body = form;
  const res = await fetch(path, opts);
  if (res.status === 401 && path !== "/api/login") {
    const body = await res.json().catch(() => ({}));
    showLogin(String(body.detail || "").toLowerCase().includes("inactiv") || String(body.detail || "").toLowerCase().includes("idle")
      ? t("login.expired_idle") : t("login.expired"));
    throw new Error("session");
  }
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  // 403 "Password change required": the session belongs to an account that must replace its
  // initial password (admin/admin or after a reset) -> reopen the forced dialog.
  if (res.status === 403 && /password change required/i.test(String(body?.detail || ""))) {
    openPasswordDialog(true);
    throw new Error("password");
  }
  if (!res.ok) throw new Error(body?.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `HTTP ${res.status}`);
  return body;
}

/**
 * URL of a stored image served by GET /api/media?path=<path> (session-checked; the server
 * only serves files inside its media folders and answers 404 for anything else).
 * @param {string} path - Server-side file path as returned by the API (image_path, thumb_path...).
 * @returns {string}
 */
const media = (path) => `/api/media?path=${encodeURIComponent(path)}`;
/**
 * Serializes a filter form into query parameters, omitting empty fields so that the server
 * applies its own defaults (e.g. no date = no bound).
 * @param {HTMLFormElement} form - Filter form.
 * @returns {URLSearchParams}
 */
const qs = (form) => { const q = new URLSearchParams(); for (const [k, v] of new FormData(form)) if (v) q.set(k, v); return q; };

// ----------------------------------------------------------------------------- session & shell
/** Current user as returned by /api/me or /api/login ({username, idle_timeout_minutes, language}). @type {?Object} */
let me = null;
/** Name of the displayed page (a key of PAGES), or null before the first navigation. @type {?string} */
let currentPage = null;
/** setInterval handle of the /api/status polling. @type {?number} */
let statusTimer = null;

/**
 * Switches to the login view: stops status polling, closes the MJPEG stream (removing the
 * <img> src aborts the long-lived HTTP response) and shows an optional notice (e.g. session
 * expired).
 * @param {string} [notice=""] - Message shown above the form; hidden when empty.
 * @returns {void}
 */
function showLogin(notice = "") {
  clearInterval(statusTimer);
  stopCameraCard();
  showSimulationCards("");
  stopLogsLive();
  if ($("#password-dialog").open) $("#password-dialog").close();
  $("#stream").removeAttribute("src");
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
  $("#login-notice").hidden = !notice;
  $("#login-notice").textContent = notice;
}

/**
 * Switches to the application shell for an authenticated user, opens the Live page and
 * starts polling GET /api/status every 5 s (background requests, see refreshStatus()).
 * @param {{username: string, idle_timeout_minutes: number, language: string}} info - Response
 *   of GET /api/me or POST /api/login.
 * @returns {Promise<void>}
 */
async function showApp(info) {
  me = info;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#whoami").textContent = info.username;
  // Software version from the central jarvis/VERSION file (served to authenticated users only).
  if (info.version) $("#app-version").textContent = `jarvis-home ${info.version}`;
  $("#idle-info").textContent = t("menu.idle", { n: info.idle_timeout_minutes });
  // Initial password still in use: nothing else is reachable until it is changed (the server
  // answers 403), so no page is loaded and no polling starts; the dialog cannot be dismissed.
  if (info.must_change_password) {
    $("#app-view").classList.add("locked");
    openPasswordDialog(true);
    return;
  }
  $("#app-view").classList.remove("locked");
  go("live");
  refreshStatus();
  statusTimer = setInterval(refreshStatus, 5000);
}

/**
 * Login form submit.
 * Calls POST /api/login with body {username, password}; on success the server sets the
 * session cookie and the app shell is shown. A 401 here means invalid credentials (api()
 * does not treat it as an expiry) and a 429 means the IP is rate-limited; the server's
 * message is shown under the form.
 */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/login", { method: "POST", json: Object.fromEntries(new FormData(e.target)) });
    $("#login-error").textContent = "";
    e.target.reset();
    showApp(r);
  } catch (err) {
    $("#login-error").textContent = err.message === "session" ? "" : t("login.failed", {}, err.message);
  }
});

/** Sign out: POST /api/logout (ends the session server-side and clears the cookie), then shows the login view. */
$("#logout").addEventListener("click", guard(async () => {
  await api("/api/logout", { method: "POST" });
  $("#user-menu").hidden = true;
  showLogin();
}));
// User menu: toggle on its button, close on any click outside the .user-menu container.
$("#user-btn").addEventListener("click", () => { $("#user-menu").hidden = !$("#user-menu").hidden; });
document.addEventListener("click", (e) => { if (!e.target.closest(".user-menu")) $("#user-menu").hidden = true; });

// ----------------------------------------------------------------------------- vehicles
/** Countries suggested in the plate form (free text is accepted too). */
const PLATE_COUNTRIES = ["France", "Belgium", "Luxembourg", "Switzerland", "Germany", "Netherlands", "Italy", "Spain",
  "Portugal", "United Kingdom", "Ireland", "Austria", "Poland", "Czech Republic", "Sweden", "Norway", "Denmark",
  "United States", "Canada", "Morocco", "Algeria", "Tunisia"];

/**
 * Vehicles page: the plate registry and the read history. Shows a notice while plate
 * recognition is disabled (core status "plates_enabled").
 * @returns {Promise<void>}
 */
async function loadVehicles() {
  $("#plates-disabled").hidden = lastStatus.plates_enabled !== false;
  const people = await api("/api/persons");
  $("#plate-person").replaceChildren(el("option", { value: "" }, "—"),
    ...people.map((p) => el("option", { value: String(p.id) }, `${p.first_name} ${p.last_name || ""}`.trim())));
  $("#plate-countries").replaceChildren(...PLATE_COUNTRIES.map((c) => el("option", { value: c })));
  await Promise.all([loadPlates(), loadPlateReads()]);
}

/** Badge of a plate status (known / disabled / expired / unknown). @param {string} st @returns {HTMLSpanElement} */
const plateBadge = (st) => badge(t(`plates.st_${st}`, {}, st), { known: "ok", disabled: "warn", expired: "bad", unknown: "bad" }[st] || "");

/** Registry table: GET /api/plates. @returns {Promise<void>} */
async function loadPlates() {
  const rows = await api("/api/plates");
  const body = $("#plates");
  if (!rows.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: "7", class: "empty" }, t("plates.none")))); return; }
  body.replaceChildren(...rows.map((r) => el("tr", {},
    el("td", {}, el("span", { class: "plate-tag", title: r.plate }, r.display || r.plate)),
    el("td", {}, r.country || ""),
    el("td", {}, r.label || ""),
    el("td", {}, `${r.first_name || ""} ${r.last_name || ""}`.trim() || "—"),
    el("td", {}, plateBadge(r.status), r.valid_until ? el("span", { class: "muted" }, ` ${t("plates.until", { d: fmtDate(r.valid_until) })}`) : ""),
    el("td", {}, r.last_read ? fmtDateTime(r.last_read) : "—"),
    el("td", { class: "right" },
      el("button", { class: "secondary small", onclick: guard(async () => {
        await api(`/api/plates/${r.id}`, { method: "PATCH", json: { enabled: !r.enabled } });
        toast(t(r.enabled ? "plates.disabled_ok" : "plates.enabled_ok", { p: r.display || r.plate }));
        loadPlates();
      }) }, t(r.enabled ? "plates.disable" : "plates.enable")),
      " ",
      el("button", { class: "ghost small", onclick: guard(async () => {
        if (!confirm(t("plates.delete_confirm", { p: r.display || r.plate }))) return;
        await api(`/api/plates/${r.id}`, { method: "DELETE" });
        toast(t("plates.deleted", { p: r.display || r.plate }));
        loadPlates();
      }) }, icon("trash"))))));
}

/**
 * Add a plate: POST /api/plates. The expiration date means "valid through that whole day"
 * (23:59:59 local time), as for the access rules of a person.
 */
$("#plate-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const json = { plate: f.get("plate"), country: f.get("country") || "", label: f.get("label") || "",
    person_id: f.get("person_id") ? Number(f.get("person_id")) : null, enabled: true };
  if (f.get("valid_until")) json.valid_until = new Date(`${f.get("valid_until")}T23:59:59`).getTime() / 1000;
  const r = await api("/api/plates", { method: "POST", json });
  toast(t("plates.added", { p: r.plate }));
  e.target.reset();
  loadPlates();
}));

/** Read history: GET /api/plate-reads with the filters. @returns {Promise<void>} */
async function loadPlateReads() {
  const rows = await api(`/api/plate-reads?${qs($("#plate-reads-filter"))}`);
  const body = $("#plate-reads");
  if (!rows.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: "9", class: "empty" }, t("plates.no_reads")))); return; }
  body.replaceChildren(...rows.map((r) => el("tr", {},
    el("td", {}, r.image_path ? el("img", { class: "vehicle", src: media(r.image_path), alt: r.plate, loading: "lazy",
      onclick: () => window.open(media(r.image_path), "_blank", "noopener") }) : ""),
    el("td", {}, fmtDateTime(r.ts)),
    el("td", {}, el("span", { class: "plate-tag" }, r.display || r.plate)),
    el("td", {}, plateBadge(r.status)),
    el("td", {}, t(`plates.dir_${r.direction}`, {}, r.direction || "")),
    el("td", {}, r.action && r.action !== "none" ? badge(t(`plates.act_${r.action}`, {}, r.action), "info") : "—"),
    el("td", {}, r.confidence != null ? `${Math.round(r.confidence * 100)} %` : ""),
    el("td", {}, r.region || ""),
    el("td", {}, [`${r.first_name || ""} ${r.last_name || ""}`.trim(), r.label].filter(Boolean).join(" · ") || "—"))));
}
$("#plate-reads-filter").addEventListener("submit", guard(async (e) => { e.preventDefault(); await loadPlateReads(); }));

// ----------------------------------------------------------------------------- logs
/** Entries displayed by the log viewer (search results, then live additions). @type {Array<Object>} */
let logEntries = [];
/** Open EventSource of the live tail, or null. @type {?EventSource} */
let logSource = null;
/** Maximum entries kept in the page (older ones are dropped from the top). */
const LOG_MAX = 2000;

/** Current filters of #logs-filter as query parameters (checkboxes as "true"). @returns {URLSearchParams} */
function logParams() {
  const f = new FormData($("#logs-filter"));
  const q = new URLSearchParams();
  for (const k of ["q", "level", "process", "start", "end"]) if (f.get(k)) q.set(k, f.get(k));
  for (const k of ["regex", "history"]) if (f.get(k)) q.set(k, "true");
  return q;
}

/** Page entry: shows the last search, or runs a first one. @returns {Promise<void>} */
async function loadLogsPage() {
  if (!logEntries.length) await searchLogs();
}

/**
 * Search: GET /api/logs with the filters (up to 1000 most recent matches). The server masks
 * secrets; a 422 (invalid regular expression, bad date) is shown as a toast by guard().
 * @returns {Promise<void>}
 */
async function searchLogs() {
  const q = logParams();
  q.set("limit", "1000");
  const r = await api(`/api/logs?${q}`);
  logEntries = r.entries;
  renderLogs();
  $("#logs-count").textContent = t("logs.count", { n: r.entries.length, total: r.total_matched })
    + (r.truncated ? ` · ${t("logs.truncated")}` : "");
}

/**
 * One row: time, level, process, logger, message. Plain-text hits are wrapped in <mark>
 * (text nodes only: nothing from the log is ever interpreted as HTML).
 * @param {Object} e - Entry {time, level, process, logger, message}.
 * @returns {HTMLDivElement}
 */
function logRow(e) {
  const f = new FormData($("#logs-filter"));
  const needle = !f.get("regex") && f.get("q") ? String(f.get("q")).toLowerCase() : "";
  const msg = el("span", { class: "msg" });
  if (needle) {
    const low = e.message.toLowerCase();
    let i = 0;
    for (let j = low.indexOf(needle); j >= 0; j = low.indexOf(needle, i)) {
      msg.append(e.message.slice(i, j), el("mark", {}, e.message.slice(j, j + needle.length)));
      i = j + needle.length;
    }
    msg.append(e.message.slice(i));
  } else msg.textContent = e.message;
  return el("div", { class: `row ${e.level}` }, el("span", { class: "time" }, e.time), el("span", { class: "lvl" }, e.level),
    el("span", { class: "proc" }, e.process), el("span", { class: "logger", title: e.logger }, e.logger), msg);
}

/** Re-renders the whole list and scrolls to the newest entry when auto-scroll is on. @returns {void} */
function renderLogs() {
  const out = $("#logs-output");
  out.replaceChildren(...(logEntries.length ? logEntries.map(logRow) : [el("p", { class: "empty" }, t("logs.empty"))]));
  if ($("#logs-autoscroll").checked) out.scrollTop = out.scrollHeight;
}

/**
 * Live additions: continuation lines (end of a traceback split across two reads) are appended
 * to the last entry; the list is capped at LOG_MAX entries.
 * @param {Array<Object>} batch - Entries received from the event stream.
 * @returns {void}
 */
function appendLogs(batch) {
  const out = $("#logs-output");
  if (!logEntries.length) out.replaceChildren();
  for (const e of batch) {
    if (e.continuation && logEntries.length) {
      const last = logEntries[logEntries.length - 1];
      last.message += `\n${e.message}`;
      out.lastElementChild?.replaceWith(logRow(last));
      continue;
    }
    logEntries.push(e);
    out.append(logRow(e));
  }
  while (logEntries.length > LOG_MAX) { logEntries.shift(); out.firstElementChild?.remove(); }
  $("#logs-count").textContent = t("logs.count", { n: logEntries.length, total: logEntries.length });
  if ($("#logs-autoscroll").checked) out.scrollTop = out.scrollHeight;
}

/**
 * Live tail: EventSource on GET /api/logs/stream with the text/level/process filters (dates and
 * history do not apply to new lines). The server ends the stream with an "end" event when the
 * session expires; the browser reconnects by itself after a network error (retry: 3 s).
 * @returns {void}
 */
function startLogsLive() {
  stopLogsLive();
  const q = logParams();
  for (const k of ["start", "end", "history"]) q.delete(k);
  logSource = new EventSource(`/api/logs/stream?${q}`);
  logSource.onmessage = (ev) => appendLogs(JSON.parse(ev.data));
  logSource.addEventListener("end", () => { stopLogsLive(); toast(t("logs.session_end"), true); });
  $("#logs-live").classList.add("live-on");
  $("#logs-live-label").textContent = t("logs.live_stop");
}

/** Stops the live tail (no-op when it is not running). @returns {void} */
function stopLogsLive() {
  if (!logSource) return;
  logSource.close();
  logSource = null;
  $("#logs-live").classList.remove("live-on");
  $("#logs-live-label").textContent = t("logs.live_start");
}

$("#logs-filter").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  await searchLogs();
  if (logSource) startLogsLive();                  // new filters for the live tail too
}));
$("#logs-live").addEventListener("click", () => (logSource ? stopLogsLive() : startLogsLive()));
$("#logs-wrap").addEventListener("change", (e) => $("#logs-output").classList.toggle("wrap", e.target.checked));
$("#logs-clear").addEventListener("click", () => { logEntries = []; renderLogs(); $("#logs-count").textContent = ""; });
// Download what is displayed, in the log file format (Blob URL, released right after the click).
$("#logs-download").addEventListener("click", () => {
  const text = logEntries.map((e) => `${e.time} ${e.level.padEnd(7)} [${e.process}] ${e.logger}: ${e.message}`).join("\n");
  const a = el("a", { href: URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain" })),
    download: `jarvis-logs-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}.txt` });
  document.body.append(a);
  a.click();
  URL.revokeObjectURL(a.href);
  a.remove();
});

// ----------------------------------------------------------------------------- password change
/** True while the dialog is in forced mode (initial password): it cannot be dismissed. @type {boolean} */
let passwordForced = false;

/**
 * Evaluates the password policy of the server (jarvis/web/app.py change_password) on the
 * current form values, for the live checklist. The server remains the authority.
 * @param {FormData} f - Values of #password-form.
 * @returns {Object<string, boolean>} Rule name -> satisfied.
 */
function passwordRules(f) {
  const cur = f.get("current_password") || "";
  const pw = f.get("new_password") || "";
  const user = (me?.username || "").toLowerCase();
  return {
    length: pw.length >= 12,
    distinct: new Set(pw).size >= 5,
    common: pw.length > 0 && ![user, "admin", "password", "jarvis"].includes(pw.toLowerCase()),
    changed: pw.length > 0 && pw !== cur,
    match: pw.length > 0 && pw === (f.get("confirm_password") || ""),
  };
}

/** Refreshes the checklist marks and enables the submit button only when every rule is met. @returns {void} */
function refreshPasswordRules() {
  const rules = passwordRules(new FormData($("#password-form")));
  for (const li of document.querySelectorAll("#password-rules li")) li.classList.toggle("ok", !!rules[li.dataset.rule]);
  $("#password-submit").disabled = !Object.values(rules).every(Boolean);
}

/**
 * Opens the password dialog.
 * @param {boolean} forced - true after a sign-in with the initial password: cannot be dismissed,
 *   the secondary button signs out; false when opened from the user menu (it just closes).
 * @returns {void}
 */
function openPasswordDialog(forced) {
  const dialog = $("#password-dialog");
  passwordForced = forced;
  $("#password-form").reset();
  $("#password-user").value = me?.username || "";
  $("#password-error").textContent = "";
  $("#password-forced").hidden = !forced;
  $("#password-secondary").textContent = forced ? t("menu.logout") : t("common.cancel");
  refreshPasswordRules();
  $("#user-menu").hidden = true;
  if (!dialog.open) dialog.showModal();
  dialog.querySelector('input[name="current_password"]').focus();
}

$("#password-form").addEventListener("input", refreshPasswordRules);
// Escape closes a modal <dialog> by default: ignored in forced mode.
$("#password-dialog").addEventListener("cancel", (e) => { if (passwordForced) e.preventDefault(); });
$("#change-password").addEventListener("click", () => openPasswordDialog(false));
// Secondary button: sign out (forced mode) or simply close.
$("#password-secondary").addEventListener("click", guard(async () => {
  $("#password-dialog").close();
  if (passwordForced) {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    showLogin();
  }
}));

/**
 * Submit: POST /api/account/password {current_password, new_password}. On success the other
 * sessions of the account are revoked by the server; in forced mode the application starts.
 * 401 = wrong current password, 422 = policy (server message shown).
 */
$("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#password-submit").disabled = true;
  try {
    const r = await api("/api/account/password", { method: "POST",
      json: { current_password: f.get("current_password"), new_password: f.get("new_password") } });
    $("#password-dialog").close();
    toast(r.other_sessions_revoked ? t("password.changed_revoked", { n: r.other_sessions_revoked }) : t("password.changed"));
    if (passwordForced) {
      passwordForced = false;
      await showApp({ ...me, must_change_password: false });
    }
  } catch (err) {
    $("#password-error").textContent = /incorrect/i.test(err.message) ? t("password.wrong_current")
      : err.message.replace(/^Password policy: /, `${t("password.policy")} `);
    refreshPasswordRules();
  }
});

/**
 * Language selectors (login footer and user menu): filled with LANGS. Changing one stores the
 * personal choice in localStorage ("jarvis.lang", which overrides the server default), loads
 * the locale and re-renders the current page so dynamically built texts are translated too.
 * localStorage may throw (private mode, disabled storage): the choice is then per-load only.
 */
for (const sel of document.querySelectorAll(".lang-select")) {
  for (const [code, name] of Object.entries(LANGS)) sel.append(el("option", { value: code }, name));
  sel.addEventListener("change", guard(async () => {
    try { localStorage.setItem("jarvis.lang", sel.value); } catch { /* private mode */ }
    await loadLocale(sel.value);
    if (currentPage) go(currentPage);
  }));
}

// ----------------------------------------------------------------------------- navigation
/**
 * Page loaders, keyed by the data-page value of the sidebar buttons (and by the "page-<name>"
 * section ids). Each loader (re)fetches the data of its page.
 * @type {Object<string, function(): (void|Promise<void>)>}
 */
const PAGES = { live: loadLive, sightings: loadSightingsPage, recordings: loadRecordingsPage, people: loadPersons, visitors: loadUnknowns,
                events: () => loadEvents(true), audit: loadAuditPage, logs: loadLogsPage, vehicles: loadVehicles,
                settings: loadSettings };

/**
 * Navigates to a page: highlights its sidebar entry, shows its section, updates the title and
 * runs its loader (errors become toasts through guard()).
 *
 * Side effects when leaving pages: the Live MJPEG stream is closed by removing the <img> src
 * (otherwise the browser keeps the multipart response open and the server keeps pushing
 * frames to a hidden image until the client disconnects), and the time-lapse player is stopped off the Recordings page.
 *
 * @param {string} page - A key of PAGES.
 * @returns {void}
 */
function go(page) {
  currentPage = page;
  for (const b of document.querySelectorAll(".nav-item")) b.classList.toggle("active", b.dataset.page === page);
  for (const p of document.querySelectorAll(".page")) p.hidden = p.id !== `page-${page}`;
  $("#page-title").textContent = t(`nav.${page}`);
  if (page !== "live") $("#stream").removeAttribute("src");   // stop the MJPEG stream off the Live page
  if (page !== "recordings") tlStop();
  if (page !== "settings") { stopCameraCard(); showSimulationCards(""); }   // previews and polling of the cards
  if (page !== "logs") stopLogsLive();            // Server-Sent Events of the live log tail
  guard(PAGES[page])();
}
// Sidebar: each .nav-item button navigates to the page named by its data-page attribute.
for (const b of document.querySelectorAll(".nav-item")) b.addEventListener("click", () => go(b.dataset.page));

// ----------------------------------------------------------------------------- live
/** Last payload of GET /api/status (core status, or {ok: false, error} when the core is down). @type {Object} */
let lastStatus = {};

/**
 * Polls GET /api/status (background request: does not extend the idle session) and updates
 * the top-bar chips (core, camera, fps) and, on the Live page, the KPI tiles.
 * The server answers 200 with {ok: false} when the core service is unreachable, so `core`
 * is false only in that case. Errors are ignored here: a 401 is already handled by api()
 * (login view), and a transient network error will be retried by the next tick.
 * @returns {Promise<void>} Never rejects.
 */
async function refreshStatus() {
  try {
    const s = await api("/api/status", { background: true });
    lastStatus = s;
    const core = s.ok !== false;
    $("#chip-core").className = `chip ${core ? "ok" : "bad"}`;
    $("#chip-camera").className = `chip ${core && s.camera_connected ? "ok" : "bad"}`;
    $("#chip-fps").textContent = core && s.vision_fps != null ? `${s.vision_fps} fps` : "– fps";
    if (currentPage === "live") renderKpis();
  } catch { /* shown by api() */ }
}

/**
 * Builds one KPI tile of the Live page.
 * @param {string} label - Caption.
 * @param {(string|number)} value - Displayed value.
 * @param {string} [kind=""] - "ok" / "bad" colors the value.
 * @returns {HTMLDivElement}
 */
function kpi(label, value, kind = "") {
  return el("div", { class: "kpi" }, el("div", { class: "label" }, label), el("div", { class: `value ${kind}` }, value));
}

/**
 * Renders the Live page KPI tiles from `lastStatus` (no request: data comes from the polling).
 * Shows a single "core unreachable" tile when the core service is down. The voice state and
 * the door state are translated ("voice.<state>", "door.<state>"), with raw/unknown fallbacks.
 * @returns {void}
 */
function renderKpis() {
  const s = lastStatus;
  const box = $("#kpis");
  box.replaceChildren();
  if (s.ok === false) { box.append(kpi(t("kpi.core"), t("kpi.unreachable"), "bad")); return; }
  const door = { open: t("door.open"), closed: t("door.closed") }[s.door] ?? t("door.unknown");
  box.append(
    kpi(t("kpi.camera"), s.camera_connected ? t("kpi.connected") : t("kpi.disconnected"), s.camera_connected ? "ok" : "bad"),
    kpi(t("kpi.fps"), s.vision_fps ?? "–"),
    kpi(t("kpi.tracks"), s.tracks ?? 0),
    kpi(t("kpi.enrolled"), s.known_embeddings ?? 0),
    // Voice: "Asleep" outside the recognition window, otherwise its state and the seconds left.
    kpi(t("kpi.voice"), s.voice_window_s > 0
      ? t("voice.window", { state: t(`voice.${s.voice_state}`, {}, s.voice_state), n: s.voice_window_s })
      : t(`voice.${s.voice_state}`, {}, s.voice_state ?? "–"), s.voice_window_s > 0 ? "ok" : ""),
    kpi(t("kpi.door"), door),
    kpi(t("kpi.authorized"), (s.authorized_persons || []).length),
  );
}

/**
 * Live page loader: (re)starts the MJPEG preview by pointing the <img> at
 * GET /api/stream.mjpg (multipart/x-mixed-replace; the server never counts this stream as
 * user activity) and renders the KPIs from the last status.
 * @returns {Promise<void>}
 */
async function loadLive() {
  $("#stream").src = "/api/stream.mjpg";
  renderKpis();
}

/** Garage button: after confirmation, POST /api/garage/pulse (relay pulse, logged by the core with the author). */
$("#btn-garage").addEventListener("click", guard(async () => {
  if (!confirm(t("live.garage_confirm"))) return;
  await api("/api/garage/pulse", { method: "POST" });
  toast(t("live.garage_sent"));
}));
/** PTZ home button: POST /api/ptz/home (moves the camera back to its home preset). */
$("#btn-home").addEventListener("click", guard(async () => { await api("/api/ptz/home", { method: "POST" }); toast(t("live.ptz_sent")); }));
/** "Speak" form: POST /api/say with body {text} (text-to-speech on the core's speaker). */
$("#say-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  await api("/api/say", { method: "POST", json: { text: e.target.text.value } });
  e.target.reset();
}));

// ----------------------------------------------------------------------------- sightings
/** Cached person list from GET /api/persons (shared by the person filters and the visitor label selects). @type {Object[]} */
let persons = [];
/** Keyset pagination cursor: id of the oldest sighting shown (sent as before_id). @type {?number} */
let oldestSighting = null;
/** Badge color per sighting status. @type {Object<string, string>} */
const STATUS_KIND = { known: "ok", labeled: "info", unknown: "bad", watchlist: "warn" };

/**
 * Display name of a sighting or search hit: the person's name when identified, otherwise
 * "Visitor #<cluster>" when the face belongs to an unknown-visitor cluster, else "Unknown".
 * @param {{first_name?: string, last_name?: string, cluster_id?: number}} s - Row from the API.
 * @returns {string}
 */
function identity(s) {
  if (s.first_name) return `${s.first_name} ${s.last_name || ""}`.trim();
  return s.cluster_id ? t("visitors.name", { n: s.cluster_id }) : t("status.unknown");
}

/**
 * Refreshes the `persons` cache from GET /api/persons and rebuilds a person <select> with an
 * "all" option first, keeping the current selection when that person still exists.
 * @param {HTMLSelectElement} select - Filter select to fill.
 * @param {string} allLabel - Label of the empty-value ("any person") option.
 * @returns {Promise<void>}
 */
async function refreshPersonSelect(select, allLabel) {
  persons = await api("/api/persons");
  const current = select.value;
  select.replaceChildren(el("option", { value: "" }, allLabel),
    ...persons.map((p) => el("option", { value: p.id }, `${p.first_name} ${p.last_name}`.trim())));
  select.value = current;
}

/**
 * Sightings page loader: person filter, first page of sightings and the 14-day chart.
 * Endpoints: GET /api/persons, then GET /api/sightings and GET /api/sightings/stats in parallel.
 * @returns {Promise<void>}
 */
async function loadSightingsPage() {
  await refreshPersonSelect($("#sightings-filter").person_id, t("filter.all_people"));
  await Promise.all([loadSightings(true), loadStats()]);
}

/**
 * Loads one page (100 rows, newest first) of sightings matching #sightings-filter.
 *
 * Endpoint: GET /api/sightings?start&end&time_from&time_to&person_id&status&limit=100[&before_id]
 * (keyset pagination: before_id is the id of the oldest row already shown). The CSV export
 * link is updated with the same filters, without limit/cursor: GET /api/sightings.csv?...
 * The "Load older" button is hidden once a page comes back shorter than the page size.
 *
 * @param {boolean} reset - true to restart from the newest row (filter change), false to append.
 * @returns {Promise<void>}
 */
async function loadSightings(reset) {
  if (reset) { oldestSighting = null; $("#sightings").replaceChildren(); }
  const q = qs($("#sightings-filter"));
  $("#sightings-csv").href = `/api/sightings.csv?${q}`;
  q.set("limit", 100);
  if (oldestSighting) q.set("before_id", oldestSighting);
  const list = await api(`/api/sightings?${q}`);
  if (reset && !list.length) $("#sightings").append(el("tr", {}, el("td", { colspan: 7, class: "empty" }, t("common.no_data"))));
  for (const s of list) {
    $("#sightings").append(el("tr", {},
      el("td", {}, fmtDate(s.ts)), el("td", {}, fmtTime(s.ts)),
      el("td", {}, s.image_path ? el("img", { class: "face", src: media(s.image_path), alt: "" }) : ""),
      el("td", {}, identity(s)), el("td", {}, badge(t(`status.${s.status}`), STATUS_KIND[s.status])),
      el("td", { class: "mono" }, s.score != null ? s.score.toFixed(2) : ""), el("td", { class: "mono" }, `#${s.track_id ?? ""}`)));
    oldestSighting = s.id;
  }
  $("#more-sightings").hidden = list.length < 100;
}

/**
 * Renders the stacked bar chart of the last 14 days (recognized vs unknown/watchlist).
 *
 * Endpoint: GET /api/sightings/stats?days=14[&time_from][&time_to] -> [{day: "YYYY-MM-DD",
 * status, n}]. Only the daily time window of the filter applies (the chart always covers the
 * last 14 days). Days without data are pre-filled with zeros using local-time keys, so that
 * they match the server's local-time day buckets. "labeled" (identified later) counts as
 * recognized. Bar heights are set through element.style (CSSOM), not a style attribute (CSP).
 *
 * @returns {Promise<void>}
 */
async function loadStats() {
  const f = $("#sightings-filter");
  const q = new URLSearchParams({ days: 14 });
  if (f.time_from.value) q.set("time_from", f.time_from.value);
  if (f.time_to.value) q.set("time_to", f.time_to.value);
  const rows = await api(`/api/sightings/stats?${q}`);
  const days = {};
  for (let i = 13; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000);
    days[`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`] = { k: 0, u: 0 };
  }
  for (const r of rows) if (days[r.day]) days[r.day][r.status === "known" || r.status === "labeled" ? "k" : "u"] += r.n;
  const max = Math.max(1, ...Object.values(days).map((d) => d.k + d.u));
  // Noon is used when re-parsing the day key so a DST shift can never move the label to another date.
  $("#sightings-stats").replaceChildren(...Object.entries(days).map(([day, d]) => el("div", {
    class: "bar", title: t("sightings.bar_title", { day: new Date(`${day}T12:00:00`).toLocaleDateString(locale), k: d.k, u: d.u }) },
    el("span", { class: "k", style: { height: `${(d.k / max) * 100}%` } }),
    el("span", { class: "u", style: { height: `${(d.u / max) * 100}%` } }),
    el("small", {}, day.slice(8)))));
}

// Filter submit reloads the list from the newest row and the chart; "Load older" appends the next page.
$("#sightings-filter").addEventListener("submit", guard(async (e) => { e.preventDefault(); await Promise.all([loadSightings(true), loadStats()]); }));
$("#more-sightings").addEventListener("click", guard(() => loadSightings(false)));

/**
 * Face search drop zone (#face-search form). Dragging a file over it highlights it; dropping
 * copies the dropped files into the form's file input and submits it, so drag-and-drop and
 * the regular file picker share the same submit path.
 */
const drop = $("#face-search");
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.file.files = e.dataTransfer.files; drop.requestSubmit(); });
/**
 * Face search submit: POST /api/search/face?start&end&time_from&time_to (multipart body with
 * the "file" field). The photo is only used to compute an embedding and is never stored.
 * The date/time filters of the Sightings page apply; person_id and status are dropped because
 * the search is precisely about finding who this is. Returns [{image_path, ts, similarity,
 * first_name, last_name, cluster_id, ...}], rendered as cards with a similarity percentage.
 */
drop.addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const form = new FormData(drop);
  const q = qs($("#sightings-filter"));
  q.delete("person_id"); q.delete("status");
  const hits = await api(`/api/search/face?${q}`, { method: "POST", form });
  const grid = $("#search-results");
  grid.replaceChildren();
  if (!hits.length) grid.append(el("p", { class: "empty" }, t("search.none")));
  for (const h of hits) {
    grid.append(el("div", { class: "card visitor" }, el("div", { class: "card-b" },
      h.image_path ? el("img", { src: media(h.image_path), alt: "" }) : "",
      el("strong", {}, identity(h)),
      el("span", { class: "muted" }, fmtDateTime(h.ts)),
      badge(t("search.similarity", { p: Math.round(h.similarity * 100) }), "info"))));
  }
}));

// ----------------------------------------------------------------------------- recordings & time-lapse
/** Keyset pagination cursor: id of the oldest clip shown. @type {?number} */
let oldestClip = null;
/** Active Recordings tab: "clips" or "timelapse" (data-rec-tab values). @type {string} */
let recTab = "clips";

/**
 * Opens the Recordings page: person filter, then the active tab (clips or time-lapse).
 * Endpoints: GET /api/persons, then loadClips() or loadTimelapse().
 * @returns {Promise<void>}
 */
async function loadRecordingsPage() {
  await refreshPersonSelect($("#rec-filter").person_id, t("filter.all_people"));
  await (recTab === "clips" ? loadClips(true) : loadTimelapse());
}

/**
 * Loads one page (60 clips) of event recordings matching the shared filter.
 *
 * Endpoint: GET /api/recordings?start&end&time_from&time_to&person_id&trigger&limit=60[&before_id]
 * -> [{id, start_ts, duration_s, size_bytes, thumb_path, triggers: string[], persons: string[]}].
 * The card color follows the most serious trigger (watchlist > unknown > other). Thumbnails
 * go through /api/media; clicking a card opens the modal player.
 *
 * @param {boolean} reset - true to restart from the newest clip, false to append older ones.
 * @returns {Promise<void>}
 */
async function loadClips(reset) {
  if (reset) { oldestClip = null; $("#clips").replaceChildren(); }
  const q = qs($("#rec-filter"));
  q.set("limit", 60);
  if (oldestClip) q.set("before_id", oldestClip);
  const list = await api(`/api/recordings?${q}`);
  if (reset && !list.length) $("#clips").append(el("p", { class: "empty" }, t("rec.none")));
  for (const r of list) {
    const kind = r.triggers.includes("watchlist") ? "warn" : r.triggers.includes("unknown") ? "bad" : "ok";
    $("#clips").append(el("div", { class: "card clip", onclick: () => openClip(r) }, el("div", { class: "card-b stack" },
      el("div", { class: "thumbwrap" }, r.thumb_path ? el("img", { src: media(r.thumb_path), alt: "" }) : el("img", { alt: "" }),
        el("span", { class: "dur" }, `${Math.round(r.duration_s)} s`), el("span", { class: "play" }, icon("play"))),
      el("strong", {}, fmtDateTime(r.start_ts)),
      el("div", { class: "toolbar" }, ...r.triggers.map((x) => badge(t(`choice.${x}`), kind))),
      el("span", { class: "muted" }, r.persons.length ? r.persons.join(", ") : t("rec.no_person")),
      el("span", { class: "muted" }, `${(r.size_bytes / 1e6).toFixed(1)} ${t("unit.MB")}`))));
    oldestClip = r.id;
  }
  $("#more-clips").hidden = list.length < 60;
}

/**
 * Plays a clip in the modal player (the server honors Range requests: seeking works).
 *
 * Endpoint: GET /api/recordings/{id}/video (MP4 H.264). Because the server answers
 * `Range: bytes=...` requests with 206 Partial Content, the <video> element can seek and
 * start playing without downloading the whole file. Autoplay may be refused by the browser
 * policy; the rejection is ignored (the user can press play).
 *
 * @param {{id: number, start_ts: number, duration_s: number, triggers: string[], persons: string[]}} r - Clip row.
 * @returns {void}
 */
function openClip(r) {
  $("#clip-title").textContent = fmtDateTime(r.start_ts);
  $("#clip-meta").textContent = `${r.triggers.map((x) => t(`choice.${x}`)).join(", ")} · ${r.persons.join(", ") || t("rec.no_person")} · ${Math.round(r.duration_s)} s`;
  const v = $("#clip-video");
  v.src = `/api/recordings/${r.id}/video`;
  $("#clip-dialog").showModal();
  v.play().catch(() => {});
}
// Closing the dialog (button or Esc) pauses playback and detaches the source; load() with no
// src aborts the pending download/range requests instead of letting them run in the background.
$("#clip-close").addEventListener("click", () => $("#clip-dialog").close());
$("#clip-dialog").addEventListener("close", () => { const v = $("#clip-video"); v.pause(); v.removeAttribute("src"); v.load(); });

/**
 * Time-lapse player: images are preloaded then shown one after the other at the chosen rate.
 * @type {{frames: Array<{ts: number, path: string}>, index: number, timer: ?number, cache: Map<string, HTMLImageElement>}}
 *  - frames: snapshot list of the selected period (from GET /api/timelapse);
 *  - index: displayed frame;
 *  - timer: setInterval handle while playing, null when paused;
 *  - cache: preloaded Image objects by path, in insertion order (Map keeps it), which makes
 *    evicting the oldest entry trivial and keeps the preloaded images referenced (a dropped
 *    Image may be garbage-collected and its download cancelled).
 */
const tl = { frames: [], index: 0, timer: null, cache: new Map() };

/**
 * Fetches the snapshot list of the filtered period/time window and shows the first frame.
 *
 * Endpoint: GET /api/timelapse?start&end&time_from&time_to -> {frames: [{ts, path}],
 * interval_s} (the server evenly sub-samples to max_frames). person_id and trigger are removed
 * from the query because snapshots are periodic, not tied to a detection. The "Export MP4"
 * link is set to GET /api/timelapse.mp4?<same filters>&fps=<speed> (server-side FFmpeg render).
 *
 * @returns {Promise<void>}
 */
async function loadTimelapse() {
  tlStop();
  const q = qs($("#rec-filter"));
  q.delete("person_id"); q.delete("trigger");
  $("#tl-export").href = `/api/timelapse.mp4?${q}&fps=${$("#tl-speed").value}`;
  const r = await api(`/api/timelapse?${q}`);
  tl.frames = r.frames;
  tl.index = 0;
  tl.cache.clear();
  $("#tl-seek").max = Math.max(0, tl.frames.length - 1);
  $("#tl-count").textContent = t("tl.count", { n: tl.frames.length, i: r.interval_s });
  if (!tl.frames.length) { $("#tl-img").removeAttribute("src"); $("#tl-stamp").textContent = t("tl.none"); return; }
  tlShow(0);
}

/**
 * Displays frame ``i`` and preloads the next ones to keep the playback smooth.
 *
 * The index is clamped to the valid range. The next 8 frames are requested ahead through
 * detached Image objects (GET /api/media?path=...), so that by the time the timer reaches
 * them the browser serves them from its cache and playback does not stutter on network
 * latency. The preload cache is bounded: above 200 entries the oldest one is evicted (one
 * per call, which is enough since at most 8 are added per call and the steady state stays
 * near the cap), limiting memory on long time-lapses.
 *
 * @param {number} i - Frame index to show.
 * @returns {void}
 */
function tlShow(i) {
  if (!tl.frames.length) return;
  tl.index = Math.min(Math.max(0, i), tl.frames.length - 1);
  const f = tl.frames[tl.index];
  $("#tl-img").src = media(f.path);
  $("#tl-stamp").textContent = fmtDateTime(f.ts);
  $("#tl-seek").value = tl.index;
  for (let k = 1; k <= 8; k++) {
    const n = tl.frames[tl.index + k];
    if (n && !tl.cache.has(n.path)) { const img = new Image(); img.src = media(n.path); tl.cache.set(n.path, img); }
  }
  if (tl.cache.size > 200) tl.cache.delete(tl.cache.keys().next().value);
}

/**
 * Pauses the time-lapse and resets the play button icon. Safe to call at any time (it is also
 * called by go() when leaving the Recordings page).
 * @returns {void}
 */
function tlStop() {
  clearInterval(tl.timer);
  tl.timer = null;
  const b = $("#tl-play");
  if (b) b.replaceChildren(icon("play"));
}

/**
 * Starts the time-lapse at the rate selected in #tl-speed (frames per second), rewinding to
 * the first frame when the end was reached; stops automatically on the last frame.
 * @returns {void}
 */
function tlPlay() {
  if (!tl.frames.length) return;
  if (tl.index >= tl.frames.length - 1) tl.index = 0;
  $("#tl-play").replaceChildren(icon("pause"));
  tl.timer = setInterval(() => {
    if (tl.index >= tl.frames.length - 1) return tlStop();
    tlShow(tl.index + 1);
  }, 1000 / Number($("#tl-speed").value));
}

// Time-lapse controls: play/pause toggle; dragging the seek bar pauses and jumps to the frame.
$("#tl-play").addEventListener("click", () => (tl.timer ? tlStop() : tlPlay()));
$("#tl-seek").addEventListener("input", (e) => { tlStop(); tlShow(Number(e.target.value)); });
// Speed change: patch the fps of the export link in place and restart the timer at the new rate.
$("#tl-speed").addEventListener("change", () => {
  $("#tl-export").href = $("#tl-export").href.replace(/fps=\d+/, `fps=${$("#tl-speed").value}`);
  if (tl.timer) { tlStop(); tlPlay(); }
});
// The shared filter reloads whichever tab is active; "Load older" appends older clips.
$("#rec-filter").addEventListener("submit", guard(async (e) => { e.preventDefault(); await loadRecordingsPage(); }));
$("#more-clips").addEventListener("click", guard(() => loadClips(false)));
/**
 * Recordings tabs (buttons with data-rec-tab="clips" | "timelapse"): marks the clicked tab
 * active, shows the matching panel (#rec-clips / #rec-timelapse) and loads its data.
 */
for (const b of document.querySelectorAll("[data-rec-tab]")) {
  b.addEventListener("click", guard(async () => {
    recTab = b.dataset.recTab;
    for (const x of document.querySelectorAll("[data-rec-tab]")) x.classList.toggle("active", x === b);
    $("#rec-clips").hidden = recTab !== "clips";
    $("#rec-timelapse").hidden = recTab !== "timelapse";
    await loadRecordingsPage();
  }));
}

// ----------------------------------------------------------------------------- people
/**
 * "Add person" form: POST /api/persons with body {first_name, last_name, can_open_garage}
 * (201), then reloads the person cards.
 */
$("#person-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const f = e.target;
  await api("/api/persons", { method: "POST", json: {
    first_name: f.first_name.value, last_name: f.last_name.value, can_open_garage: f.can_open_garage.checked } });
  f.reset();
  toast(t("people.created"));
  loadPersons();
}));

/**
 * People page loader: GET /api/persons, then one card per person (each card fetches its own
 * face list, see personCard()). Cards are built sequentially to keep the list order stable.
 * @returns {Promise<void>}
 */
async function loadPersons() {
  persons = await api("/api/persons");
  const grid = $("#persons");
  grid.replaceChildren();
  if (!persons.length) grid.append(el("p", { class: "empty" }, t("people.none")));
  for (const p of persons) grid.append(await personCard(p));
}

/**
 * Builds a switch-style checkbox (styled by `.switch` in style.css: hidden input + track span).
 * @param {boolean} checked - Initial state.
 * @param {function(Event): *} onchange - Change listener.
 * @returns {HTMLLabelElement}
 */
function toggle(checked, onchange) {
  return el("label", { class: "switch" }, el("input", { type: "checkbox", checked, onchange }), el("span"));
}

/**
 * Builds the card of one person: avatar (first enrolled face, or initials), garage-access and
 * watchlist switches, access-rule editor, enrolled face thumbnails, counters and actions.
 *
 * Endpoints:
 *  - GET    /api/persons/{id}/faces             face list (for the avatar and thumbnails);
 *  - PATCH  /api/persons/{id}                   {can_open_garage} | {watchlist} | access rules;
 *  - POST   /api/persons/{id}/faces             multipart "file", one request per selected photo
 *                                               (a failing photo, e.g. no face found, only
 *                                               produces a toast; the others are still sent);
 *  - DELETE /api/faces/{face_id}                remove one enrolled face;
 *  - POST   /api/persons/{id}/voice             via recordVoice();
 *  - DELETE /api/persons/{id}/voice             clear the voice prints;
 *  - DELETE /api/persons/{id}                   delete the person (after confirmation).
 * Toggling the watchlist reloads the list because it changes the card style and badges;
 * toggling garage access does not (the switch already shows the new state).
 *
 * @param {Object} p - Person from GET /api/persons ({id, first_name, last_name, can_open_garage,
 *   watchlist, last_seen, face_count, auto_face_count, voice_count, access_days, access_start,
 *   access_end, valid_until}).
 * @returns {Promise<HTMLDivElement>} The card element.
 */
async function personCard(p) {
  const faces = await api(`/api/persons/${p.id}/faces`);
  const patch = (json) => api(`/api/persons/${p.id}`, { method: "PATCH", json });
  const initials = `${p.first_name[0] || ""}${p.last_name?.[0] || ""}`.toUpperCase();
  const fileInput = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true,
    onchange: guard(async (e) => {
      for (const file of e.target.files) {
        const form = new FormData();
        form.append("file", file);
        try { await api(`/api/persons/${p.id}/faces`, { method: "POST", form }); }
        catch (err) { toast(`${file.name}: ${err.message}`, true); }
      }
      loadPersons();
    }) });
  const avatar = faces[0] ? el("img", { class: "avatar", src: media(faces[0].image_path), alt: "" }) : el("div", { class: "avatar" }, initials);
  return el("div", { class: `card person-card ${p.watchlist ? "watch" : ""}` }, el("div", { class: "card-b" },
    el("div", { class: "person-head" }, avatar, el("div", {},
      el("h3", {}, `${p.first_name} ${p.last_name}`.trim(), " ",
        p.watchlist ? badge(t("people.watchlist_badge"), "bad") : (p.can_open_garage ? badge(t("people.access_badge"), "ok") : "")),
      el("div", { class: "muted" }, p.last_seen ? t("people.last_seen", { d: fmtDateTime(p.last_seen) }) : t("people.never_seen")))),
    el("label", { class: "check" }, toggle(p.can_open_garage, guard((e) => patch({ can_open_garage: e.target.checked }))), t("people.can_open")),
    el("label", { class: "check" }, toggle(p.watchlist, guard(async (e) => { await patch({ watchlist: e.target.checked }); loadPersons(); })), t("people.watchlist")),
    accessEditor(p, patch),
    el("div", { class: "thumbs" }, ...faces.map((f) => el("div", { class: "thumb", title: t(`source.${f.source}`, {}, f.source) },
      el("img", { src: media(f.image_path), alt: "" }),
      f.source === "auto" ? el("span", { class: "tag" }, "AUTO") : "",
      el("button", { class: "x", title: t("common.delete"), onclick: guard(async () => {
        await api(`/api/faces/${f.id}`, { method: "DELETE" }); loadPersons(); }) }, "×")))),
    el("div", { class: "muted" }, t("people.counts", { f: p.face_count, a: p.auto_face_count, v: p.voice_count })),
    el("div", { class: "toolbar" }, fileInput,
      el("button", { class: "secondary small", onclick: () => fileInput.click() }, icon("plus"), t("people.add_photos")),
      el("button", { class: "secondary small", onclick: guard(() => recordVoice(p)) }, icon("mic"), t("people.add_voice")),
      p.voice_count ? el("button", { class: "ghost small", onclick: guard(async () => {
        await api(`/api/persons/${p.id}/voice`, { method: "DELETE" }); loadPersons(); }) }, t("people.clear_voice")) : "",
      el("div", { class: "grow" }),
      el("button", { class: "danger small icon-only", title: t("common.delete"), onclick: guard(async () => {
        if (!confirm(t("people.delete_confirm", { name: p.first_name }))) return;
        await api(`/api/persons/${p.id}`, { method: "DELETE" }); toast(t("people.deleted")); loadPersons();
      }) }, icon("trash")))));
}

/** Weekday translation keys, Monday first; ISO weekday number = index + 1. @type {string[]} */
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

/**
 * Builds the collapsible access-rule editor of a person (allowed weekdays, daily time slot,
 * expiry date).
 *
 * Wire format sent through `patch` (PATCH /api/persons/{id}):
 *  - access_days: string of ISO weekday digits ("135" = Mon, Wed, Fri); all 7 checked is sent
 *    as "" which means "no day restriction" (a missing value also shows every day checked);
 *  - access_start / access_end: "HH:MM" or ""; both or neither must be set (checked here);
 *  - valid_until: Unix seconds at 23:59:59 local time of the chosen date (the access stays
 *    valid for the whole last day), or clear_valid_until: true when the date is emptied.
 *
 * @param {Object} p - Person row (access_days, access_start, access_end, valid_until).
 * @param {function(Object): Promise<*>} patch - Sends a PATCH for this person.
 * @returns {HTMLDetailsElement}
 * @throws {Error} From the Save handler (caught by guard()) when only one time bound is set.
 */
function accessEditor(p, patch) {
  const days = DAY_KEYS.map((d, i) => el("label", {},
    el("input", { type: "checkbox", value: String(i + 1), checked: !p.access_days || p.access_days.includes(String(i + 1)) }),
    t(`day.${d}`)));
  const start = el("input", { type: "time", value: p.access_start || "" });
  const end = el("input", { type: "time", value: p.access_end || "" });
  // valid_until -> YYYY-MM-DD for the date input, in LOCAL time: the stored value is 23:59:59
  // local time, and toISOString() (UTC) would show the next day west of UTC.
  const until = el("input", { type: "date", value: p.valid_until ? localDateInput(p.valid_until) : "" });
  const restricted = p.access_days || p.access_start || p.valid_until;
  return el("details", { class: "panel" },
    el("summary", {}, t("rules.title"), badge(restricted ? t("rules.restricted") : t("rules.permanent"), restricted ? "warn" : "")),
    el("div", {},
      el("div", { class: "days" }, ...days),
      el("div", { class: "toolbar" }, el("label", { class: "field" }, t("rules.from"), start), el("label", { class: "field" }, t("rules.to"), end)),
      el("label", { class: "field" }, t("rules.until"), until),
      el("button", { class: "small", onclick: guard(async () => {
        const checked = days.map((d) => d.querySelector("input")).filter((c) => c.checked).map((c) => c.value).join("");
        if ((start.value === "") !== (end.value === "")) throw new Error(t("rules.both_times"));
        const json = { access_days: checked.length === 7 ? "" : checked, access_start: start.value, access_end: end.value };
        if (until.value) json.valid_until = new Date(`${until.value}T23:59:59`).getTime() / 1000; else json.clear_valid_until = true;
        await patch(json);
        toast(t("rules.saved"));
        loadPersons();
      }) }, t("common.save"))));
}

/** Cancels the voice recording in progress (set by recordVoice(), called by the dialog's Cancel button). @type {?function(): void} */
let cancelRecording = null;
/**
 * Records a 10-second voice sample from the microphone and enrolls it for a person.
 *
 * Uses getUserMedia (requires a secure context: HTTPS or localhost) and MediaRecorder; a
 * modal dialog shows the countdown. The recording stops after 10 s or on Cancel; the
 * microphone tracks are always released. The sample (browser-native container, typically
 * WebM/Opus, named "voice.webm") is sent unless cancelled.
 * Endpoint: POST /api/persons/{id}/voice (multipart "file"; the server decodes and extracts
 * a speaker embedding).
 *
 * @param {{id: number}} p - Person to enroll.
 * @returns {Promise<void>}
 * @throws {DOMException} If microphone access is denied or unavailable.
 * @throws {Error} If the upload is rejected (e.g. audio too short or not decodable).
 */
async function recordVoice(p) {
  const dialog = $("#record-dialog");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream);
  const chunks = [];
  let cancelled = false;
  rec.ondataavailable = (e) => chunks.push(e.data);
  const done = new Promise((resolve) => (rec.onstop = resolve));
  cancelRecording = () => { cancelled = true; rec.stop(); };
  dialog.showModal();
  rec.start();
  for (let s = 10; s > 0 && rec.state === "recording"; s--) {
    $("#record-status").textContent = t("voice.remaining", { s });
    await new Promise((r) => setTimeout(r, 1000));
  }
  if (rec.state === "recording") rec.stop();
  await done;
  stream.getTracks().forEach((tr) => tr.stop());
  dialog.close();
  if (cancelled) return;
  const form = new FormData();
  form.append("file", new Blob(chunks, { type: rec.mimeType }), "voice.webm");
  await api(`/api/persons/${p.id}/voice`, { method: "POST", form });
  toast(t("voice.saved"));
  loadPersons();
}
$("#record-cancel").addEventListener("click", () => cancelRecording?.());

// ----------------------------------------------------------------------------- visitors
/**
 * Builds the "assign to..." select of a visitor cluster: existing persons, plus "new person"
 * which prompts for a first name. Picking an entry calls `onPick` with either
 * {person_id: number} (the select value is a string, hence the Number() conversion) or
 * {new_person: {first_name, last_name: "", can_open_garage: false}}.
 * Uses the `persons` cache, which must be loaded beforehand.
 * @param {function(Object): Promise<*>} onPick - Receives the label request body.
 * @returns {HTMLSelectElement}
 */
function labelSelect(onPick) {
  const select = el("select", {}, el("option", { value: "" }, t("visitors.assign")),
    ...persons.map((p) => el("option", { value: p.id }, `${p.first_name} ${p.last_name}`.trim())),
    el("option", { value: "new" }, t("visitors.new_person")));
  select.addEventListener("change", guard(async () => {
    if (!select.value) return;
    let json;
    if (select.value === "new") {
      const first = prompt(t("people.first_name"));
      if (!first) { select.value = ""; return; }
      json = { new_person: { first_name: first, last_name: "", can_open_garage: false } };
    } else json = { person_id: Number(select.value) };
    await onPick(json);
  }));
  return select;
}

/**
 * Visitors page loader: unknown faces grouped into clusters (the same face coming back).
 *
 * Endpoints:
 *  - GET    /api/unknowns/clusters and GET /api/persons (in parallel);
 *  - POST   /api/unknowns/clusters/{cluster_id}/label  body {person_id} | {new_person}; the
 *           faces are enrolled for that person and past sightings of the cluster are relabeled
 *           (response {faces, sightings_relabeled});
 *  - DELETE /api/unknowns/clusters/{cluster_id}        after confirmation.
 * @returns {Promise<void>}
 */
async function loadUnknowns() {
  const [clusters, ps] = await Promise.all([api("/api/unknowns/clusters"), api("/api/persons")]);
  persons = ps;
  const grid = $("#unknowns");
  grid.replaceChildren();
  if (!clusters.length) grid.append(el("p", { class: "empty" }, t("visitors.none")));
  for (const c of clusters) {
    grid.append(el("div", { class: "card visitor" }, el("div", { class: "card-b" },
      el("img", { src: media(c.image_path), alt: "" }),
      el("strong", {}, t("visitors.name", { n: c.cluster_id })),
      el("span", { class: "muted" }, t("visitors.stats", { v: c.faces, s: c.sightings })),
      el("span", { class: "muted" }, c.faces > 1 ? `${fmtDate(c.first_seen)} → ${fmtDate(c.last_seen)}` : fmtDateTime(c.last_seen)),
      labelSelect(async (json) => {
        const r = await api(`/api/unknowns/clusters/${c.cluster_id}/label`, { method: "POST", json });
        toast(t("visitors.labeled", { f: r.faces, s: r.sightings_relabeled }));
        loadUnknowns();
      }),
      el("button", { class: "secondary small", onclick: guard(async () => {
        if (!confirm(t("visitors.delete_confirm"))) return;
        await api(`/api/unknowns/clusters/${c.cluster_id}`, { method: "DELETE" }); loadUnknowns();
      }) }, icon("trash"), t("common.delete")))));
  }
}

// ----------------------------------------------------------------------------- events
/** Keyset pagination cursor: id of the oldest event shown. @type {?number} */
let oldestEvent = null;
/**
 * Translated label of an event type ("event.<type>"), or the raw type when untranslated.
 * @param {string} type - Event type (e.g. "garage_pulse").
 * @returns {string}
 */
const eventLabel = (type) => t(`event.${type}`, {}, type);

/**
 * Compact JSON of an event's details without the fields already shown in their own column
 * or not useful to display (ip, session_id).
 * @param {Object} d - Event details.
 * @returns {string} JSON text, or "" when nothing remains.
 */
function detailsText(d) {
  const x = { ...d };
  delete x.ip; delete x.session_id;
  return Object.keys(x).length ? JSON.stringify(x) : "";
}

/**
 * Loads one page (100 rows) of the full event log (system, vision, voice, administration).
 * Endpoint: GET /api/events?start&end&time_from&time_to&type&limit=100[&before_id].
 * @param {boolean} reset - true to restart from the newest event (also fills the type datalist).
 * @returns {Promise<void>}
 */
async function loadEvents(reset) {
  if (reset) { oldestEvent = null; $("#events").replaceChildren(); fillEventTypes(); }
  const q = qs($("#events-filter"));
  q.set("limit", 100);
  if (oldestEvent) q.set("before_id", oldestEvent);
  const list = await api(`/api/events?${q}`);
  for (const ev of list) {
    $("#events").append(el("tr", {}, el("td", {}, fmtDateTime(ev.ts)), el("td", {}, badge(eventLabel(ev.type), eventKind(ev.type))),
      el("td", {}, ev.actor || ""), el("td", {}, ev.first_name || ""), el("td", { class: "mono" }, detailsText(ev.details))));
    oldestEvent = ev.id;
  }
  $("#more-events").hidden = list.length < 100;
}
/**
 * Badge color of an event type, inferred from its name: failures/threats are red, successful
 * access is green, configuration changes are orange, anything else is neutral.
 * @param {string} type - Event type.
 * @returns {string} "bad", "ok", "warn" or "".
 */
function eventKind(type) {
  if (/denied|failed|watchlist|unknown|revoked|timeout|rejected|refused|abandoned/.test(type)) return "bad";
  if (/pulse|recognized|login$|vehicle_left|plate_read|window_opened/.test(type)) return "ok";
  if (/changed|updated|deleted|reset/.test(type)) return "warn";
  return "";
}
/**
 * Fills the #event-types <datalist> (type/action filter suggestions) once, from the
 * "event.*" keys of the en-US reference table, which is the list of known event types.
 * @returns {void}
 */
function fillEventTypes() {
  const dl = $("#event-types");
  if (dl.children.length) return;
  for (const k of Object.keys(fallback).filter((k) => k.startsWith("event."))) dl.append(el("option", { value: k.slice(6) }, t(k)));
}
$("#events-filter").addEventListener("submit", guard(async (e) => { e.preventDefault(); await loadEvents(true); }));
$("#more-events").addEventListener("click", guard(() => loadEvents(false)));

// ----------------------------------------------------------------------------- audit
/** Keyset pagination cursor: id of the oldest audit row shown. @type {?number} */
let oldestAudit = null;

/**
 * Audit page loader: type suggestions, then the first page of administrative actions.
 * @returns {Promise<void>}
 */
async function loadAuditPage() { fillEventTypes(); await loadAudit(true); }

/**
 * Renders the "Changes" cell of an audit row.
 *  - details.changes (settings_changed, person_updated...): one line per field,
 *    "label: before -> after" (before struck through). The server builds this diff with
 *    secret values already masked as "(set)" / "(empty)", so write-only secrets never
 *    reach the browser, even in the audit trail;
 *  - details.values: raw JSON (e.g. values of a creation);
 *  - otherwise: the remaining details (see detailsText()).
 * @param {{details: Object}} ev - Audit event.
 * @returns {HTMLDivElement}
 */
function renderChanges(ev) {
  const d = ev.details;
  const list = d.changes || [];
  if (list.length) {
    return el("div", { class: "diff" }, ...list.map((c) => el("div", { class: "d" },
      el("b", {}, t(`set.${c.field}`, {}, c.field)),
      el("span", { class: "from" }, JSON.stringify(c.before)), " → ", el("span", { class: "to" }, JSON.stringify(c.after)))));
  }
  if (d.values) return el("div", { class: "mono" }, JSON.stringify(d.values));
  return el("div", { class: "mono" }, detailsText(d));
}

/**
 * Loads one page (100 rows) of the audit trail (administrative actions only).
 *
 * Endpoint: GET /api/audit?start&end&time_from&time_to&actor&type&limit=100[&before_id].
 * The CSV link is updated with the same filters: GET /api/audit.csv?... (includes each row's
 * chain hash; the export itself is audited). The "Target" column is the most specific
 * subject found in the details (person, name, account, revoked user or setting keys).
 *
 * @param {boolean} reset - true to restart from the newest row, false to append older ones.
 * @returns {Promise<void>}
 */
async function loadAudit(reset) {
  if (reset) { oldestAudit = null; $("#audit").replaceChildren(); }
  const q = qs($("#audit-filter"));
  $("#audit-csv").href = `/api/audit.csv?${q}`;
  q.set("limit", 100);
  if (oldestAudit) q.set("before_id", oldestAudit);
  const list = await api(`/api/audit?${q}`);
  if (reset && !list.length) $("#audit").append(el("tr", {}, el("td", { colspan: 6, class: "empty" }, t("common.no_data"))));
  for (const ev of list) {
    const target = ev.first_name || ev.details.name || ev.details.username || ev.details.revoked_user
      || (ev.details.keys ? ev.details.keys.join(", ") : "");
    $("#audit").append(el("tr", {}, el("td", {}, fmtDateTime(ev.ts)), el("td", {}, ev.actor || ""),
      el("td", { class: "mono" }, ev.details.ip || ""), el("td", {}, badge(eventLabel(ev.type), eventKind(ev.type))),
      el("td", {}, target), el("td", {}, renderChanges(ev))));
    oldestAudit = ev.id;
  }
  $("#more-audit").hidden = list.length < 100;
}

$("#audit-filter").addEventListener("submit", guard(async (e) => { e.preventDefault(); await loadAudit(true); }));
$("#more-audit").addEventListener("click", guard(() => loadAudit(false)));
/**
 * "Verify integrity": GET /api/audit/verify recomputes the SHA-256 hash chain of the event log
 * -> {ok, checked, last_hash} or {ok: false, broken_at, reason}. The result is shown in a
 * banner; the list is then reloaded because the verification itself is audited
 * (audit_verified) and appears as the newest row.
 */
$("#verify-btn").addEventListener("click", guard(async () => {
  const r = await api("/api/audit/verify");
  const b = $("#verify-banner");
  b.hidden = false;
  b.className = `banner ${r.ok ? "ok" : "bad"}`;
  b.replaceChildren(icon("shield"), el("span", { class: "grow" }, r.ok
    ? t("audit.chain_ok", { n: r.checked, h: (r.last_hash || "").slice(0, 16) })
    : t("audit.chain_broken", { id: r.broken_at, reason: r.reason })));
  loadAudit(true);
}));

/**
 * Audit tabs (buttons with data-audit-tab="actions" | "sessions"): show the matching panel;
 * the sessions table is (re)loaded each time its tab is opened.
 */
for (const b of document.querySelectorAll("[data-audit-tab]")) {
  b.addEventListener("click", guard(async () => {
    for (const x of document.querySelectorAll("[data-audit-tab]")) x.classList.toggle("active", x === b);
    $("#audit-actions").hidden = b.dataset.auditTab !== "actions";
    $("#audit-sessions").hidden = b.dataset.auditTab !== "sessions";
    if (b.dataset.auditTab === "sessions") await loadSessions();
  }));
}

/**
 * Renders the sessions table (active and ended sessions, with end reason and duration).
 *
 * Endpoints: GET /api/sessions -> [{id, username, current, ip, user_agent, created_at,
 * last_seen, ended_at, active, end_reason, duration_s}]; DELETE /api/sessions/{id} revokes
 * another active session. The current session has no Revoke button (use Sign out instead).
 * A normal logout is shown neutral; other end reasons (idle timeout, expiry, revoked) orange.
 * @returns {Promise<void>}
 */
async function loadSessions() {
  const rows = await api("/api/sessions");
  $("#sessions").replaceChildren(...rows.map((s) => el("tr", {},
    el("td", {}, s.username, s.current ? badge(t("sessions.you"), "info") : ""),
    el("td", { class: "mono" }, s.ip || "—"), el("td", { class: "muted" }, (s.user_agent || "").slice(0, 48)),
    el("td", {}, fmtDateTime(s.created_at)), el("td", {}, fmtDateTime(s.last_seen)), el("td", {}, fmtDateTime(s.ended_at)),
    el("td", {}, s.active ? badge(t("sessions.active"), "ok") : badge(t(`reason.${s.end_reason}`, {}, s.end_reason || ""), s.end_reason === "logout" ? "" : "warn")),
    el("td", {}, fmtDuration(s.duration_s)),
    el("td", {}, s.active && !s.current ? el("button", { class: "secondary small", onclick: guard(async () => {
      await api(`/api/sessions/${s.id}`, { method: "DELETE" }); toast(t("sessions.revoked")); loadSessions();
    }) }, t("sessions.revoke")) : ""))));
}

// ----------------------------------------------------------------------------- settings
/** Last GET /api/settings payload: {groups: [{name, params: [...]}]}. @type {?Object} */
let settingsModel = null;
/**
 * Pending (unsaved) edits: setting key -> new value, already coerced to the parameter's type.
 * A key is removed as soon as its value equals the effective one again.
 * @type {Map<string, *>}
 */
const dirty = new Map();
/** Name of the displayed settings group (kept across reloads when it still exists). @type {?string} */
let activeGroup = null;

/**
 * Settings page loader: GET /api/settings, drops the pending edits, keeps the selected group
 * when possible, renders it and loads the monitoring card (GET /api/monitoring; failures are
 * ignored because the card is optional).
 * @returns {Promise<void>}
 */
async function loadSettings() {
  settingsModel = await api("/api/settings");
  dirty.clear();
  updateSavebar();
  if (!(activeGroup && settingsModel.groups.some((g) => g.name === activeGroup))) {
    let saved = null;
    try { saved = localStorage.getItem("jarvis.settings.tab"); } catch { /* private mode */ }
    activeGroup = settingsModel.groups.some((g) => g.name === saved) ? saved : settingsModel.groups[0]?.name;
  }
  renderSettings();
  loadMonitoring().catch(() => {});
}

/**
 * Main tabs (one per domain), with a dot on the domains holding unsaved edits. Also called by
 * updateSavebar() after every edit. No request.
 * @returns {void}
 */
function renderDomainTabs() {
  if (!settingsModel) return;
  const domain = settingsModel.groups.find((x) => x.name === activeGroup)?.domain;
  const domains = settingsModel.domains || [...new Set(settingsModel.groups.map((x) => x.domain))];
  const dirtyDomains = new Set(settingsModel.groups.filter((x) => x.params.some((p) => dirty.has(p.key))).map((x) => x.domain));
  $("#settings-domains").replaceChildren(...domains.map((d) => el("button", {
    class: `tab-btn ${d === domain ? "active" : ""}`, role: "tab",
    onclick: () => { selectGroup(settingsModel.groups.find((x) => x.domain === d).name); } },
    t(`dom.${d}`, {}, d), dirtyDomains.has(d) ? el("span", { class: "dot", title: t("settings.unsaved") }) : "")));
}

/**
 * Renders the two-level navigation (domain tabs, group sub-tabs) and the parameters of the
 * active group. Group titles are translated through the key of their first parameter
 * ("grp.<first key>"), domains through "dom.<domain>". The monitoring, camera and simulation
 * cards follow the active group. No request.
 * @returns {void}
 */
function renderSettings() {
  const domain = settingsModel.groups.find((x) => x.name === activeGroup)?.domain;
  renderDomainTabs();
  // Sub-tabs: the groups of the active domain.
  $("#settings-nav").replaceChildren(...settingsModel.groups.filter((x) => x.domain === domain).map((x) => el("button", {
    class: x.name === activeGroup ? "active" : "", role: "tab", onclick: () => selectGroup(x.name) },
    t(`grp.${x.params[0].key}`, {}, x.name))));
  const g = settingsModel.groups.find((x) => x.name === activeGroup);
  $("#settings-groups").replaceChildren(el("div", { class: "card" },
    el("div", { class: "card-h" }, el("h2", {}, t(`grp.${g.params[0].key}`, {}, g.name))),
    el("div", { class: "card-b flush" }, ...g.params.map(paramRow))));
  $("#monitoring-card").hidden = !g.params.some((p) => p.key.startsWith("monitoring."));
  showSimulationCards(g.name);
  if (g.params.some((p) => p.key === "camera.rtsp_url")) startCameraCard(); else stopCameraCard();
}

/**
 * Opens a settings group (sub-tab) and remembers it for the next visit.
 * @param {string} name - Group name, as sent by GET /api/settings.
 * @returns {void}
 */
function selectGroup(name) {
  activeGroup = name;
  try { localStorage.setItem("jarvis.settings.tab", name); } catch { /* private mode */ }
  renderSettings();
}

// ----------------------------------------------------------------------------- simulation
/** setInterval handle refreshing the simulation cards while one is visible. @type {?number} */
let simTimer = null;
/** Last GET /api/simulation payload ({files, camera_simulation, max_upload_mb}). @type {Object} */
let simState = { files: [] };

/**
 * Shows the simulation card matching the settings group ("Camera simulation" or "Voice
 * simulation"), refreshes it every 3 s while visible, and stops everything otherwise.
 * @param {string} group - Name of the displayed settings group.
 * @returns {void}
 */
function showSimulationCards(group) {
  const camera = group === "Camera simulation";
  const voice = group === "Voice simulation";
  $("#sim-camera-card").hidden = !camera;
  $("#sim-voice-card").hidden = !voice;
  if (!camera) $("#sim-stream").removeAttribute("src");
  clearInterval(simTimer);
  simTimer = null;
  if (camera || voice) {
    refreshSimulation().catch(() => {});
    if (voice) fillSimPersons().catch(() => {});
    simTimer = setInterval(() => refreshSimulation().catch(() => {}), 3000);
  }
}

/** Person selector of the voice simulation (only people allowed to open the garage make the window open). @returns {Promise<void>} */
async function fillSimPersons() {
  const people = await api("/api/persons");
  $("#sim-person").replaceChildren(...people.map((p) => el("option", { value: String(p.id) },
    `${p.first_name} ${p.last_name || ""}`.trim() + (p.can_open_garage ? "" : ` (${t("sim.not_allowed")})`))));
}

/**
 * GET /api/simulation: media list (with Play / Delete for videos and photos), audio selector,
 * and the camera source state; the preview stream runs only while a file is playing.
 * @returns {Promise<void>}
 */
async function refreshSimulation() {
  simState = await api("/api/simulation", { background: true });
  const playing = simState.camera_simulation;
  const st = $("#sim-camera-state");
  st.textContent = playing ? t("sim.playing", { f: playing }) : t("sim.live_camera");
  st.className = `badge ${playing ? "info" : ""}`;
  $("#sim-camera-stop").hidden = !playing;
  const img = $("#sim-stream");
  if (playing && !$("#sim-camera-card").hidden) {
    if (!img.getAttribute("src")) img.src = "/api/stream.mjpg";
    $("#sim-stream-empty").hidden = true;
  } else {
    img.removeAttribute("src");
    $("#sim-stream-empty").hidden = false;
  }
  const media = simState.files.filter((f) => f.kind === "video" || f.kind === "image");
  $("#sim-media-list").replaceChildren(...(media.length ? media.map((f) => el("div", { class: `sim-file ${f.name === playing ? "playing" : ""}` },
    badge(t(`sim.kind_${f.kind}`, {}, f.kind)), el("span", { class: "grow", title: f.name }, f.name),
    el("span", { class: "muted" }, `${(f.size / 1048576).toFixed(1)} MB`),
    el("button", { class: "small", onclick: guard(async () => {
      await api("/api/simulation/camera", { method: "POST", json: { file: f.name } });
      toast(t("sim.started", { f: f.name }));
      await refreshSimulation();
    }) }, icon("play"), t("sim.play")),
    el("button", { class: "ghost small", title: t("common.delete"), onclick: guard(async () => {
      if (f.name === playing) await api("/api/simulation/camera", { method: "POST", json: { file: null } });
      await api(`/api/simulation/files/${encodeURIComponent(f.name)}`, { method: "DELETE" });
      await refreshSimulation();
    }) }, icon("trash")))) : [el("p", { class: "muted" }, t("sim.no_media"))]));
  const audio = simState.files.filter((f) => f.kind === "audio");
  const sel = $("#sim-audio");
  const keep = sel.value;
  sel.replaceChildren(...(audio.length ? audio.map((f) => el("option", { value: f.name }, f.name)) : [el("option", { value: "" }, t("sim.no_audio"))]));
  if (audio.some((f) => f.name === keep)) sel.value = keep;
}

/**
 * Uploads a simulation file (POST /api/simulation/files, multipart "file").
 * @param {File} file - Selected file.
 * @returns {Promise<Object>} {name, kind, size}.
 */
async function uploadSimFile(file) {
  if (file.size > (simState.max_upload_mb || 100) * 1048576) throw new Error(t("sim.too_big", { n: simState.max_upload_mb || 100 }));
  const form = new FormData();
  form.append("file", file);
  toast(t("sim.uploading", { f: file.name }));
  const r = await api("/api/simulation/files", { method: "POST", form });
  await refreshSimulation();
  return r;
}

$("#sim-media-upload").addEventListener("change", guard(async (e) => {
  if (!e.target.files.length) return;
  const r = await uploadSimFile(e.target.files[0]);
  e.target.value = "";
  await api("/api/simulation/camera", { method: "POST", json: { file: r.name } });   // play it at once
  toast(t("sim.started", { f: r.name }));
  await refreshSimulation();
}));
$("#sim-camera-stop").addEventListener("click", guard(async () => {
  await api("/api/simulation/camera", { method: "POST", json: { file: null } });
  toast(t("sim.stopped"));
  await refreshSimulation();
}));
$("#sim-audio-upload").addEventListener("change", guard(async (e) => {
  if (!e.target.files.length) return;
  const r = await uploadSimFile(e.target.files[0]);
  e.target.value = "";
  $("#sim-audio").value = r.name;
}));

/**
 * Renders the outcome of a simulation: the stages (wake word, speech recognition, transcript,
 * intent), what the decision engine logged (badges) and what Jarvis would have said.
 * @param {Object} r - Response of POST /api/simulation/voice or /api/simulation/face.
 * @returns {void}
 */
function showSimResult(r) {
  const out = $("#sim-voice-result");
  out.hidden = false;
  const yes = (v) => (v ? badge(t("common.yes"), "ok") : badge(t("common.no"), "bad"));
  const rows = [];
  if (r.person !== undefined) {
    rows.push([t("sim.recognized"), r.person], [t("sim.window"), r.window_s ? t("sim.window_open", { n: r.window_s }) : t("sim.window_closed")]);
  } else {
    rows.push([t("sim.wake_word"), yes(r.wake_word)], [t("sim.stt"), r.stt || "—"], [t("sim.transcript"), r.transcript || "—"],
      [t("sim.intent"), r.intent ? t(`sim.intent_${r.intent}`, {}, r.intent) : "—"], [t("sim.outcome"), r.outcome || "—"]);
  }
  rows.push([t("sim.decisions"), r.events?.length ? el("span", {}, ...r.events.map((e) => [badge(eventLabel(e.type), eventKind(e.type)), " "]).flat()) : "—"],
    [t("sim.said"), r.said?.length ? r.said.map((x) => `« ${x} »`).join(" ") : "—"]);
  out.replaceChildren(el("ul", { class: "sim-steps" }, ...rows.map(([k, v]) => el("li", {}, el("span", { class: "k" }, k), el("span", {}, v)))));
}

$("#sim-face-btn").addEventListener("click", guard(async () => {
  if (!$("#sim-person").value) throw new Error(t("sim.no_person"));
  showSimResult(await api("/api/simulation/face", { method: "POST", json: { person_id: Number($("#sim-person").value) } }));
}));
for (const b of document.querySelectorAll("[data-sim-phrase]")) {
  b.addEventListener("click", () => { $("#sim-voice-form").elements.text.value = b.dataset.simPhrase; });
}
$("#sim-voice-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  $("#sim-voice-result").hidden = false;
  $("#sim-voice-result").replaceChildren(el("p", { class: "muted" }, t("sim.running")));
  showSimResult(await api("/api/simulation/voice", { method: "POST", json: { text: e.target.elements.text.value } }));
}));
$("#sim-audio-btn").addEventListener("click", guard(async () => {
  if (!$("#sim-audio").value) throw new Error(t("sim.no_audio"));
  $("#sim-voice-result").hidden = false;
  $("#sim-voice-result").replaceChildren(el("p", { class: "muted" }, t("sim.running")));
  showSimResult(await api("/api/simulation/voice", { method: "POST", json: { file: $("#sim-audio").value } }));
}));

// ----------------------------------------------------------------------------- camera stream card
/** setInterval handle of the camera card KPI polling (null when the card is hidden). @type {?number} */
let cameraTimer = null;
/** Timeout that reveals the "no image" notice when the preview does not start. @type {?number} */
let cameraNoFrame = null;

/**
 * Shows the camera card (Settings > Camera stream): starts the annotated MJPEG preview
 * (GET /api/stream.mjpg, the same stream as the Live page, drawn by the analysis in progress),
 * the KPI polling every 2 s, and pre-fills the stream to test with the URL being edited
 * (unsaved value) or the saved one. Idempotent.
 * @returns {void}
 */
function startCameraCard() {
  $("#camera-card").hidden = false;
  const url = dirty.has("camera.rtsp_url") ? dirty.get("camera.rtsp_url")
    : settingsModel.groups.flatMap((g) => g.params).find((p) => p.key === "camera.rtsp_url")?.value;
  if (!$("#probe-url").value) $("#probe-url").value = url || "";
  if (cameraTimer) return;
  const img = $("#settings-stream");
  $("#settings-stream-empty").hidden = true;
  img.onload = () => { clearTimeout(cameraNoFrame); $("#settings-stream-empty").hidden = true; };
  img.onerror = () => { $("#settings-stream-empty").hidden = false; };
  cameraNoFrame = setTimeout(() => { $("#settings-stream-empty").hidden = false; }, 5000);
  img.src = "/api/stream.mjpg";
  refreshCameraKpis();
  cameraTimer = setInterval(refreshCameraKpis, 2000);
}

/** Hides the camera card and stops its stream (removing src aborts the HTTP response) and polling. @returns {void} */
function stopCameraCard() {
  if (!cameraTimer && $("#camera-card").hidden) return;
  $("#camera-card").hidden = true;
  $("#settings-stream").removeAttribute("src");
  clearInterval(cameraTimer);
  clearTimeout(cameraNoFrame);
  cameraTimer = null;
}

/**
 * Analysis indicators of the camera card, from GET /api/status (background request: the
 * polling does not extend the idle session): camera link, analysis rate, people tracked,
 * enrolled faces. The core being down is shown as a single tile.
 * @returns {Promise<void>}
 */
async function refreshCameraKpis() {
  const box = $("#camera-kpis");
  try {
    const s = await api("/api/status", { background: true });
    if (s.ok === false) { box.replaceChildren(kpi(t("kpi.core"), t("kpi.unreachable"), "bad")); return; }
    box.replaceChildren(
      kpi(t("camera.kpi_stream"), s.camera_connected ? t("camera.connected") : t("camera.disconnected"), s.camera_connected ? "ok" : "bad"),
      kpi(t("camera.kpi_rate"), `${s.vision_fps ?? 0} fps`),
      kpi(t("camera.kpi_tracks"), String(s.tracks ?? 0)),
      kpi(t("camera.kpi_faces"), String(s.known_embeddings ?? 0)));
  } catch { /* 401 handled by api(); transient errors retried by the next tick */ }
}

/**
 * Stream test: POST /api/camera/probe {url} (the saved URL when the field is empty). The server
 * injects the camera account of the Secrets and runs ffprobe over TCP; the result lists codec,
 * resolution, rate and audio, then the Jarvis advice (H.265, small or huge resolution, high rate).
 */
$("#probe-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#probe-result");
  const btn = $("#probe-btn");
  btn.disabled = true;
  out.hidden = false;
  out.replaceChildren(el("p", { class: "muted" }, t("camera.probing")));
  try {
    const r = await api("/api/camera/probe", { method: "POST", json: { url: $("#probe-url").value.trim() || null } });
    if (!r.ok) {
      out.replaceChildren(el("div", { class: "probe-bad" }, `${t("camera.probe_failed")} ${r.error || ""}`));
      return;
    }
    const v = r.video;
    const rows = [
      [t("camera.codec"), `${(v.codec || "?").toUpperCase()}${v.profile ? ` (${v.profile})` : ""}`],
      [t("camera.resolution"), `${v.width} × ${v.height}`],
      [t("camera.rate"), v.fps ? `${v.fps} fps` : "—"],
      [t("camera.audio"), r.audio ? r.audio.codec : t("camera.none")],
      [t("camera.elapsed"), `${r.elapsed_s} s`],
    ];
    out.replaceChildren(el("div", { class: "probe-ok" },
      el("strong", {}, t("camera.probe_ok")),
      el("dl", {}, ...rows.flatMap(([k, val]) => [el("dt", {}, k), el("dd", {}, val)])),
      r.warnings.length ? el("ul", { class: "probe-warn" }, ...r.warnings.map((w) => el("li", {}, w))) : ""));
  } catch (err) {
    if (err.message !== "session") out.replaceChildren(el("div", { class: "probe-bad" }, err.message));
  } finally {
    btn.disabled = false;
  }
});

/**
 * Builds the row of one setting: label, badges, help, key, and a control matching its type.
 *
 * Edits are not sent immediately: `set(v)` records the value in `dirty` (or forgets it when
 * it equals the effective value, compared as JSON so lists compare by content), flags the row
 * and updates the save bar. Values are coerced to the type the server validates:
 *  - bool:  switch -> boolean;
 *  - enum:  <select>; option values are always strings in the DOM, so when every choice is
 *           numeric (e.g. a model input size) the value is converted back to a Number,
 *           otherwise the server would reject "10" for an int enum or see a spurious change;
 *           ui.language choices are labeled with their native language name;
 *  - list:  textarea, one item per line, trimmed, empty lines dropped;
 *  - events / multi: checkbox group -> array of checked values;
 *  - int / float: number input with min/max/step; an empty field is ignored (not sent);
 *  - secret: write-only. The server never returns the value (only is_set and its source), so
 *           the password field starts empty; typing a value marks it dirty, clearing the field
 *           cancels the edit (keeps the stored secret), and "Clear" explicitly sets "" to erase
 *           it. autocomplete="new-password" keeps the browser from filling a saved password;
 *  - anything else: text input.
 * "Reset" (overridden settings only) calls POST /api/settings/reset with {keys: [key]} to
 * drop the database override and return to the config.yaml value, then reloads the page.
 *
 * @param {Object} p - Parameter from GET /api/settings ({key, label, help, type, min, max,
 *   step, unit, choices, hot, value, default, source, overridden, updated_by, is_set}).
 * @returns {HTMLDivElement}
 */
function paramRow(p) {
  const row = el("div", { class: `param ${dirty.has(p.key) ? "dirty" : ""}` });
  const value = dirty.has(p.key) ? dirty.get(p.key) : p.value;
  const set = (v) => {
    const same = JSON.stringify(v) === JSON.stringify(p.value);
    if (same) dirty.delete(p.key); else dirty.set(p.key, v);
    row.classList.toggle("dirty", !same);
    updateSavebar();
  };
  let control;
  if (p.type === "bool") control = el("label", { class: "check" }, toggle(!!value, (e) => set(e.target.checked)), t(value ? "common.on" : "common.off"));
  else if (p.type === "enum") control = el("select", { onchange: (e) => set(p.choices.every((c) => /^\d+$/.test(c)) ? Number(e.target.value) : e.target.value) },
    ...p.choices.map((c) => el("option", { value: c, selected: String(c) === String(value) }, p.key === "ui.language" ? LANGS[c] : t(`choice.${c}`, {}, c))));
  else if (p.type === "list") control = el("textarea", { rows: 3, oninput: (e) => set(e.target.value.split("\n").map((x) => x.trim()).filter(Boolean)) },
    (value || []).join("\n"));
  else if (p.type === "events" || p.type === "multi") control = el("div", { class: "stack" }, ...p.choices.map((c) => el("label", { class: "check" },
    el("input", { type: "checkbox", checked: (value || []).includes(c), onchange: () => {
      set([...row.querySelectorAll("input:checked")].map((i) => i.value)); } , value: c }),
    p.type === "multi" ? t(`choice.${c}`, {}, c) : eventLabel(c))));
  else if (p.type === "int" || p.type === "float") control = el("div", { class: "inline" },
    el("input", { type: "number", min: p.min ?? "", max: p.max ?? "", step: p.step ?? (p.type === "int" ? 1 : "any"), value: value ?? "",
      oninput: (e) => e.target.value !== "" && set(p.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value)) }),
    p.unit ? el("span", { class: "muted" }, t(`unit.${p.unit}`, {}, p.unit)) : "");
  else if (p.type === "secret") control = el("div", { class: "inline" },
    el("input", { type: "password", autocomplete: "new-password", value: dirty.has(p.key) ? value : "",
      placeholder: p.is_set ? t("settings.secret_keep") : t("settings.secret_empty"), oninput: (e) => {
        if (e.target.value) set(e.target.value); else { dirty.delete(p.key); row.classList.remove("dirty"); updateSavebar(); } } }),
    p.is_set ? el("button", { class: "ghost small", type: "button", onclick: () => { set(""); toast(t("settings.secret_cleared")); } },
      t("settings.secret_clear")) : "");
  else control = el("input", { value: value ?? "", placeholder: p.extra?.placeholder ?? "", oninput: (e) => set(e.target.value) });

  // Badges: cold setting (needs a core restart), database override (with author), secret
  // state and origin (file/environment/parameter), or a value forced by the environment.
  const meta = [];
  if (!p.hot) meta.push(badge(t("settings.restart_badge"), "warn"));
  if (p.overridden) meta.push(badge(t("settings.overridden", { by: p.updated_by || "" }), "info"));
  if (p.type === "secret") meta.push(badge(p.is_set ? t("settings.secret_set", { src: t(`source_cfg.${p.source}`, {}, p.source) })
                                                    : t("settings.secret_unset"), p.is_set ? "ok" : "warn"));
  else if (p.source === "environment") meta.push(badge(t("source_cfg.environment"), "info"));
  // The config.yaml default is displayed for every setting except secrets (the server sends null for them).
  row.append(
    el("div", {}, el("div", { class: "t" }, t(`set.${p.key}`, {}, p.label), ...meta),
      el("div", { class: "h" }, t(`help.${p.key}`, {}, p.help)),
      el("div", { class: "k" }, p.type === "secret" ? p.key : `${p.key} · ${t("settings.yaml_value")}: ${JSON.stringify(p.default)}`)),
    el("div", { class: "ctl" }, control,
      p.overridden ? el("button", { class: "ghost small", onclick: guard(async () => {
        const r = await api("/api/settings/reset", { method: "POST", json: { keys: [p.key] } });
        afterSave(r); toast(t("settings.reset_done")); await loadSettings();
      }) }, t("settings.reset")) : ""));
  return row;
}

/**
 * Shows the sticky save bar while there are pending edits, with their count.
 * @returns {void}
 */
function updateSavebar() {
  renderDomainTabs();
  $("#savebar").hidden = dirty.size === 0;
  $("#dirty-count").textContent = t("settings.dirty", { n: dirty.size });
}

/**
 * Shows the "restart needed" banner when the save/reset response lists cold settings
 * (restart_required), i.e. settings the core only reads at startup.
 * @param {{restart_required?: string[]}} r - Response of PUT /api/settings or POST /api/settings/reset.
 * @returns {void}
 */
function afterSave(r) {
  if (r.restart_required?.length) $("#restart-banner").hidden = false;
}

// Cancel: forget every pending edit and re-render from the loaded model (no request).
$("#settings-cancel").addEventListener("click", () => { dirty.clear(); updateSavebar(); renderSettings(); });
/**
 * Save: PUT /api/settings with body {changes: {key: value, ...}} -> {changes: [diff], restart_required,
 * core_reloaded}. The server validates every value (422 on error, nothing saved), stores the
 * overrides, audits a diff with secrets masked and hot-reloads the core. When ui.language was
 * changed, the personal locale choice is dropped so the new server default applies at once.
 */
$("#settings-save").addEventListener("click", guard(async () => {
  const r = await api("/api/settings", { method: "PUT", json: { changes: Object.fromEntries(dirty) } });
  afterSave(r);
  toast(t("settings.saved", { n: r.changes.length }));
  if (dirty.has("ui.language")) { try { localStorage.removeItem("jarvis.lang"); } catch { /* ignore */ } await loadLocale(dirty.get("ui.language")); }
  await loadSettings();
}));
/** "Restart now": after confirmation, POST /api/core/restart (systemd respawns the core, which then applies cold settings). */
$("#restart-core").addEventListener("click", guard(async () => {
  if (!confirm(t("settings.restart_confirm"))) return;
  await api("/api/core/restart", { method: "POST" });
  $("#restart-banner").hidden = true;
  toast(t("settings.restarting"));
}));

/** Whether the metrics bearer token is displayed in clear. @type {boolean} */
let tokenVisible = false;
/**
 * Loads the monitoring card: GET /api/monitoring -> {metrics_enabled, metrics_token,
 * metrics_url, status}. The token is masked by default; its value is kept in a data attribute
 * so "Show" can toggle it without another request. `status` is the last report of the root
 * monitoring applier (applied_at, message).
 * @returns {Promise<void>}
 */
async function loadMonitoring() {
  const m = await api("/api/monitoring");
  $("#metrics-token").textContent = tokenVisible ? m.metrics_token : "•".repeat(24);
  $("#metrics-token").dataset.token = m.metrics_token;
  const s = m.status || {};
  $("#monitoring-status").textContent = s.applied_at
    ? t("monitoring.status", { d: fmtDateTime(s.applied_at), m: s.message || "" }) : t("monitoring.never");
}
// Show/hide the metrics token (no request).
$("#token-show").addEventListener("click", () => {
  tokenVisible = !tokenVisible;
  $("#metrics-token").textContent = tokenVisible ? $("#metrics-token").dataset.token : "•".repeat(24);
});
/**
 * Rotate the token: POST /api/monitoring/token (the previous token stops working immediately),
 * then reloads and reveals the new one so it can be copied into the Prometheus configuration.
 */
$("#token-rotate").addEventListener("click", guard(async () => {
  if (!confirm(t("monitoring.rotate_confirm"))) return;
  await api("/api/monitoring/token", { method: "POST" }); tokenVisible = true; await loadMonitoring();
}));
/**
 * Re-apply agents: POST /api/monitoring/apply republishes the desired monitoring state for the
 * root applier service, which runs asynchronously; the status line is refreshed after 3 s.
 */
$("#monitoring-apply").addEventListener("click", guard(async () => {
  await api("/api/monitoring/apply", { method: "POST" }); toast(t("monitoring.applied"));
  setTimeout(() => loadMonitoring().catch(() => {}), 3000);
}));

// ----------------------------------------------------------------------------- boot
/**
 * Boot sequence (never leaves a blank page: every failure ends on the login view):
 *  1. choose the locale: personal choice (localStorage "jarvis.lang") > server default from
 *     GET /api/ui (public, no session needed, so the login screen is already translated) >
 *     browser language > en-US; load it (a missing table keeps the English texts);
 *  2. opened as a local file (file://): there is no API to talk to, so show the login view
 *     with an explanation and disable the form;
 *  3. GET /api/me: 200 -> application shell (or the forced password dialog); 401 -> login view
 *     without any "session expired" notice (a first visit is not an expiry); network error or
 *     5xx -> login view with a "server unreachable" notice.
 */
(async () => {
  const fileMode = location.protocol === "file:";
  let pref = null;
  try { pref = localStorage.getItem("jarvis.lang"); } catch { /* private mode */ }
  const server = fileMode ? {} : await fetch("/api/ui").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  await loadLocale(pref || server.language || navigator.language || "en-US");
  if (fileMode) {
    showLogin(t("login.file_mode", {}, "This page is served by the Jarvis appliance: open https://jarvis.local/ (or the address of the mini-PC). Opened as a local file, sign-in is not possible."));
    for (const n of $("#login-form").elements) n.disabled = true;
    return;
  }
  try {
    const r = await fetch("/api/me", { headers: { "X-Jarvis": "1" }, credentials: "same-origin" });
    if (r.ok) return showApp(await r.json());
    if (r.status === 401) return showLogin();
    showLogin(t("login.unreachable", {}, "The Jarvis service is not answering. Check that jarvis-api is running."));
  } catch {
    showLogin(t("login.unreachable", {}, "The Jarvis service is not answering. Check that jarvis-api is running."));
  }
})();
