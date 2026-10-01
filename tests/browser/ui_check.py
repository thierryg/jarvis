# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/browser/ui_check.py
# Purpose : Real-browser check of the web UI (Playwright + Chrome) against a live API
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Real-browser check of the UI: file:// boot, forced password dialog, images, locales, unreachable API.

Not collected by pytest (needs Chrome and Playwright). Usage::

    make ui-check                         # or: python tests/browser/ui_check.py <screenshot-dir>

Starts the API on 127.0.0.1:8765 with a throw-away database (factory account admin/admin), then
drives Google Chrome headless through four scenarios and saves screenshots. Exit code 0 when
every check passes, 1 otherwise.
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from jarvis.config.settings import Settings  # noqa: E402
from jarvis.storage.database import Database  # noqa: E402
from jarvis.web import probe  # noqa: E402
from jarvis.web.app import create_app  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="jarvis-ui-"))
OUT.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp())
s = Settings()
s.storage.data_dir = str(tmp)
s.api.cookie_secure = False
# A preview frame (as written by the core) and a log file (as written by the services).
s.vision.frame_path = str(tmp / "frame.jpg")
from PIL import Image, ImageDraw  # noqa: E402

img = Image.new("RGB", (960, 540), (12, 20, 32))
ImageDraw.Draw(img).rectangle((380, 120, 580, 500), outline=(0, 220, 120), width=4)
img.save(s.vision.frame_path, "JPEG")
s.logging.file_path = str(tmp / "jarvis.log")
Path(s.logging.file_path).write_text(
    "2026-09-30 10:00:00,001 INFO    [core] jarvis.core.service: Core started\n"
    "2026-09-30 10:00:01,002 WARNING [core] jarvis.vision.camera: RTSP stream lost, reconnecting\n"
    "2026-09-30 10:00:02,003 ERROR   [api] jarvis.web.app: Unhandled error\nTraceback (most recent call last):\n  boom\n")


async def fake_probe(url, timeout_s=15.0):
    """Stands for ffprobe (no camera in the test): an H.265 1080p stream."""
    return {"ok": True, "elapsed_s": 0.4, "video": {"codec": "hevc", "profile": "Main", "width": 1920, "height": 1080,
                                                    "fps": 25.0}, "audio": {"codec": "aac"},
            "warnings": probe.advice({"codec": "hevc", "width": 1920, "height": 1080, "fps": 25.0}), "error": None}


probe.probe = fake_probe
db = Database(s.storage.db_path)
db.init()
core = type("Core", (), {"call": lambda self, cmd, **k: {"ok": True, "camera_connected": False}})()
app = create_app(s, db, core)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
time.sleep(1.5)
URL = "http://127.0.0.1:8765/"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)

    # 1. file:// -> login view with an explanation, form disabled, no JS error.
    page = browser.new_page(locale="en-US")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((REPO / "jarvis/web/static/index.html").as_uri())
    page.wait_for_timeout(800)
    check("file:// shows the login view", page.is_visible("#login-view"))
    check("file:// explains how to open Jarvis", "jarvis.local" in page.inner_text("#login-notice"))
    check("file:// disables the form", page.is_disabled("#login-form button[type=submit]"))
    check("file:// no JavaScript error", not errors, "; ".join(errors))
    page.screenshot(path=str(OUT / "01-file-mode.png"))
    page.close()

    # 2. Served by the API: login -> forced dialog -> change -> app.
    ctx = browser.new_context(locale="en-US", viewport={"width": 1366, "height": 860})
    page = ctx.new_page()
    errors, failed = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("requestfailed", lambda r: failed.append(r.url))
    page.goto(URL)
    page.wait_for_selector("#login-view:not([hidden])")
    check("first visit: no 'session expired' notice", page.is_hidden("#login-notice"))
    served = {a: page.evaluate("u => fetch(u, {cache: 'no-store'}).then(r => r.status)", a)
              for a in ("img/login-bg.svg", "img/favicon.svg", "img/favicon.ico", "img/apple-touch-icon.png", "manifest.webmanifest")}
    check("images and manifest are served (200)", all(v == 200 for v in served.values()), str(served))
    bg = page.evaluate("getComputedStyle(document.querySelector('.login-hero')).backgroundImage")
    check("login hero uses img/login-bg.svg", "login-bg.svg" in bg, bg)
    page.screenshot(path=str(OUT / "02-login.png"))

    page.fill("input[name=username]", "admin")
    page.fill("#login-form input[name=password]", "admin")
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#password-dialog[open]")
    check("admin/admin opens the forced password dialog", page.is_visible("#password-forced"))
    check("the application is locked behind the dialog", "locked" in page.get_attribute("#app-view", "class"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Escape does not close the forced dialog", page.evaluate("document.querySelector('#password-dialog').open"))
    page.fill("#password-form input[name=current_password]", "admin")
    page.fill("#password-form input[name=new_password]", "short")
    page.fill("#password-form input[name=confirm_password]", "short")
    check("a weak password keeps the submit disabled", page.is_disabled("#password-submit"))
    page.screenshot(path=str(OUT / "03-forced-dialog-weak.png"))
    strong = "Garage-Door-2026!"
    page.fill("#password-form input[name=new_password]", strong)
    page.fill("#password-form input[name=confirm_password]", strong)
    oks = page.eval_on_selector_all("#password-rules li.ok", "els => els.length")
    check("all five rules turn green", oks == 5, str(oks))
    page.screenshot(path=str(OUT / "04-forced-dialog-ok.png"))
    page.click("#password-submit")
    page.wait_for_selector("#password-dialog:not([open])", state="attached")
    page.wait_for_timeout(800)
    check("the dialog closes after the change", not page.evaluate("document.querySelector('#password-dialog').open"))
    check("the application is unlocked", "locked" not in page.get_attribute("#app-view", "class"))
    check("the Live page is shown", page.is_visible("#page-live"))
    check("the version comes from jarvis/VERSION", "jarvis-home" in page.inner_text("#app-version"))
    page.screenshot(path=str(OUT / "05-app-after-change.png"))

    page.reload()
    page.wait_for_timeout(1000)
    check("after reload: straight to the application", page.is_visible("#app-view") and not page.evaluate("document.querySelector('#password-dialog').open"))
    page.click("#user-btn")
    page.click("#change-password")
    page.wait_for_selector("#password-dialog[open]")
    check("voluntary mode: no forced notice", page.is_hidden("#password-forced"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("voluntary mode: Escape closes the dialog", not page.evaluate("document.querySelector('#password-dialog').open"))
    # Settings > Camera stream: annotated preview, KPIs, stream test, URL validation.
    page.click('.nav-item[data-page="settings"]')
    page.wait_for_selector("#camera-card:not([hidden])")
    check("the Camera stream group comes first", page.inner_text("#settings-nav button.active").strip() == "Camera stream")
    page.wait_for_function("document.querySelector('#settings-stream').naturalWidth > 0", timeout=8000)
    check("the annotated live preview is displayed", page.evaluate("document.querySelector('#settings-stream').naturalWidth") == 960)
    page.wait_for_selector("#camera-kpis .kpi")
    check("analysis indicators are shown", page.eval_on_selector_all("#camera-kpis .kpi", "k => k.length") == 4)
    page.fill("#probe-url", "rtsp://10.0.0.9:554/live")
    page.click("#probe-btn")
    page.wait_for_selector("#probe-result .probe-ok")
    res = page.inner_text("#probe-result")
    check("stream test reports codec, resolution and the H.265 advice", "HEVC" in res and "1920 × 1080" in res and "H.265" in res, res)
    page.fill("#probe-url", "rtsp://admin:pw@10.0.0.9/live")
    page.click("#probe-btn")
    page.wait_for_selector("#probe-result .probe-bad")
    check("a URL with credentials is refused by the test", "Secrets" in page.inner_text("#probe-result"))
    page.screenshot(path=str(OUT / "07-settings-camera.png"), full_page=True)
    rtsp_input = page.locator('.param:has-text("RTSP stream URL") input').first
    if rtsp_input.count():
        rtsp_input.fill("rtsp://10.0.0.9:554/live")
        page.click("#settings-save")
        page.wait_for_timeout(700)
        check("saving the stream URL asks for a core restart", page.is_visible("#restart-banner"))

    # Logs: search, highlight, level filter, live tail.
    page.click('.nav-item[data-page="logs"]')
    page.wait_for_selector("#logs-output .row")
    check("the log page lists the entries", page.eval_on_selector_all("#logs-output .row", "r => r.length") == 3)
    check("the traceback belongs to its entry", "boom" in page.inner_text("#logs-output .row.ERROR"))
    page.fill('#logs-filter input[name="q"]', "stream")
    page.click("#logs-filter button")
    page.wait_for_timeout(500)
    check("text search keeps the matching entry and highlights it",
          page.eval_on_selector_all("#logs-output .row", "r => r.length") == 1 and page.inner_text("#logs-output mark").lower() == "stream")
    page.fill('#logs-filter input[name="q"]', "")
    page.select_option('#logs-filter select[name="level"]', "ERROR")
    page.click("#logs-filter button")
    page.wait_for_timeout(500)
    check("level filter", page.eval_on_selector_all("#logs-output .row", "r => r.length") == 1)
    page.select_option('#logs-filter select[name="level"]', "INFO")
    page.click("#logs-filter button")
    page.click("#logs-live")
    page.wait_for_timeout(1500)
    with open(s.logging.file_path, "a") as fh:
        fh.write("2026-09-30 10:05:00,000 INFO    [core] jarvis.vision.pipeline: live line with password=hunter2\n")
    page.wait_for_function("document.querySelector('#logs-output').innerText.includes('live line')", timeout=8000)
    live_text = page.inner_text("#logs-output")
    check("the live tail shows new lines, secrets masked", "live line" in live_text and "hunter2" not in live_text)
    page.screenshot(path=str(OUT / "08-logs-live.png"))
    page.click("#logs-live")

    # Vehicles: add a plate, status, disable, read history with snapshot, delete.
    (tmp / "plates" / "20260930").mkdir(parents=True, exist_ok=True)
    snap = tmp / "plates" / "20260930" / "car.jpg"
    Image.new("RGB", (320, 200), (40, 60, 90)).save(snap, "JPEG")
    db.add_plate_read("ZZ999ZZ", 0.93, "Germany", "unknown", None, 4, "approaching", str(snap))
    page.click('.nav-item[data-page="vehicles"]')
    page.wait_for_selector("#plate-reads tr")
    page.fill('#plate-form input[name="plate"]', "ab-123-cd")
    page.fill('#plate-form input[name="country"]', "France")
    page.fill('#plate-form input[name="label"]', "Clio")
    page.click("#plate-form button")
    page.wait_for_selector("#plates .plate-tag")
    check("a plate can be registered (normalized, displayed as typed)",
          page.inner_text("#plates .plate-tag") == "AB-123-CD" and "Known" in page.inner_text("#plates"))
    page.click('#plates button.secondary')
    page.wait_for_timeout(600)
    check("a plate can be disabled", "Disabled" in page.inner_text("#plates"))
    page.wait_for_function("document.querySelector('#plate-reads img.vehicle')?.naturalWidth > 0", timeout=5000)
    check("the read history shows the snapshot and the status",
          "ZZ999ZZ" in page.inner_text("#plate-reads") and "Unknown" in page.inner_text("#plate-reads"))
    page.screenshot(path=str(OUT / "09-vehicles.png"), full_page=True)
    page.once("dialog", lambda d: d.accept())
    page.click("#plates button.ghost")
    page.wait_for_timeout(600)
    check("a plate can be deleted (audited)", "No plate registered" in page.inner_text("#plates")
          and len(db.list_events(type_="plate_deleted")) == 1)
    check("served mode: no JavaScript error", not errors, "; ".join(errors))
    ctx.close()

    # 3. French locale: the dialog is translated.
    ctx = browser.new_context(locale="fr-FR")
    page = ctx.new_page()
    page.goto(URL)
    page.wait_for_selector("#login-view:not([hidden])")
    page.select_option("#login-form .lang-select", "fr-FR")
    page.wait_for_timeout(400)
    title = page.evaluate("document.querySelector('[data-i18n=\"password.title\"]').textContent")
    check("French translation of the dialog", title == "Changez votre mot de passe", title)
    ctx.close()

    # 4. API unreachable: the page still ends on the login view with a notice.
    ctx = browser.new_context(locale="en-US")
    page = ctx.new_page()
    page.route("**/api/me", lambda route: route.abort())
    page.goto(URL)
    page.wait_for_timeout(800)
    check("unreachable API: login view with a notice", page.is_visible("#login-view") and "not answering" in page.inner_text("#login-notice"))
    page.screenshot(path=str(OUT / "06-api-unreachable.png"))
    ctx.close()
    browser.close()

server.should_exit = True
print(f"\n{sum(ok for _, ok in results)}/{len(results)} checks passed · screenshots: {OUT}")
sys.exit(0 if all(ok for _, ok in results) else 1)
