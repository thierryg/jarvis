# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/console.py
# Purpose : pfSense-style local appliance console (tty1) for offline administration
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Local appliance console (pfSense style), shown on the physical screen of the mini-PC (tty1).

Started by ``jarvis-console.service`` on tty1 in place of the login prompt, or manually with
``sudo jarvis console``. It displays the system state (addresses, services, camera) and offers a
numbered menu for the operations that must remain possible without the web interface:
password reset, service restart, logs, configuration check, hardware test, backup/restore,
settings reset, audit verification, reboot/shutdown and a root shell.

Access is protected by a web administrator account (``console.require_login``), because the
console runs as root. Every sensitive action (login, password reset, restart, restore, settings
reset, session revocation, reboot, shell access...) is written to the hash-chained audit log
with the console user as author and ``via="console"``.
"""

from __future__ import annotations

import getpass
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

from jarvis import __version__
from jarvis.config.settings import Settings
from jarvis.storage.database import Database

SERVICES = ("jarvis-core", "jarvis-api", "nginx", "jarvis-backup.timer")
BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"


def run(cmd: list[str], timeout: float = 15) -> str:
    """Runs a system command and returns its output (empty string when unavailable)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def clear() -> None:
    """Clear the terminal and move the cursor to the top-left corner (ANSI escape codes)."""
    print("\033[2J\033[H", end="")


def pause() -> None:
    """Wait for Enter so the operator can read the output before the banner is redrawn."""
    input(f"\n{DIM}Press Enter to return to the menu…{RESET}")


def confirm(question: str) -> bool:
    """Ask a yes/no question; anything other than "y"/"yes" means no (safe default)."""
    return input(f"{YELLOW}{question} [y/N] {RESET}").strip().lower() in ("y", "yes")


class Console:
    """Interactive menu. Each ``do_*`` method implements one menu entry.

    Attributes:
        s: Loaded settings.
        db: Database handle (the console runs as root on the appliance itself).
        user: Name of the logged-in web administrator, used as the audit author.
    """

    def __init__(self, settings: Settings):
        """Open the database and make sure its schema is up to date."""
        self.s = settings
        self.db = Database(settings.storage.db_path)
        self.db.init()

    # --- Display ---------------------------------------------------------------------------
    def interfaces(self) -> list[tuple[str, str, str]]:
        """(interface, state, IPv4 addresses) for every interface except loopback."""
        out = []
        for line in run(["ip", "-br", "-4", "addr"]).splitlines():
            parts = line.split()
            if parts and parts[0] != "lo":
                out.append((parts[0], parts[1] if len(parts) > 1 else "?", " ".join(parts[2:]) or "-"))
        return out

    def service_state(self, name: str) -> str:
        """Return the systemd state of a unit, colorized (green active, yellow transitional, red otherwise)."""
        state = run(["systemctl", "is-active", name]) or "unknown"
        color = GREEN if state == "active" else (YELLOW if state in ("activating", "reloading") else RED)
        return f"{color}{state}{RESET}"

    def core_status(self) -> dict:
        """Query the core status over the control socket (short timeout); empty dict if unreachable."""
        from jarvis.core.control import ControlClient, CoreUnavailable

        try:
            return ControlClient(self.s.control.socket_path, timeout=3).call("status")
        except (CoreUnavailable, ValueError):
            return {}

    def banner(self) -> None:
        """Clear the screen and draw the status header: host, interfaces, web URL, services, core."""
        clear()
        uptime = run(["uptime", "-p"]).replace("up ", "")
        print(f"{BOLD}{CYAN}JARVIS appliance console{RESET}  {DIM}jarvis-home {__version__} · "
              f"{socket.gethostname()} · up {uptime}{RESET}")
        print("─" * 78)
        for name, state, addrs in self.interfaces():
            print(f"  {name:<16} {state:<8} {addrs}")
        ifaces = self.interfaces()
        # Web URL: first address of an UP interface, else any address, else loopback.
        ip = next((a.split()[0].split("/")[0] for _, st, a in ifaces if st == "UP" and a != "-"),
                  next((a.split()[0].split("/")[0] for _, _, a in ifaces if a != "-"), "127.0.0.1"))
        print(f"  {'Web interface':<16}          https://{ip}/")
        print("─" * 78)
        print("  " + "   ".join(f"{s.replace('.timer', '')}: {self.service_state(s)}" for s in SERVICES))
        st = self.core_status()
        if st.get("ok"):
            cam = f"{GREEN}connected{RESET}" if st.get("camera_connected") else f"{RED}disconnected{RESET}"
            print(f"  camera: {cam}   vision: {st.get('vision_fps')} fps   tracks: {st.get('tracks')}   "
                  f"enrolled faces: {st.get('known_embeddings')}   door: {st.get('door') or 'n/a'}")
        else:
            print(f"  core: {RED}not reachable{RESET}")
        print("─" * 78)

    # (key, label, action): the action maps to the ``do_<action>`` method.
    MENU = (
        ("1", "Refresh status", "status"),
        ("2", "Network configuration", "network"),
        ("3", "Reset / create a web administrator", "password"),
        ("4", "Restart Jarvis services", "restart"),
        ("5", "Live logs (core)", "logs"),
        ("6", "Check configuration and models", "check"),
        ("7", "Hardware test (LEDs, door sensor, relay)", "hardware"),
        ("8", "Back up now / list backups", "backup"),
        ("9", "Restore a backup", "restore"),
        ("10", "Reset web settings to config.yaml values", "reset_settings"),
        ("11", "Verify audit log integrity", "verify"),
        ("12", "Active web sessions / revoke all", "sessions"),
        ("13", "Reboot the system", "reboot"),
        ("14", "Shut down the system", "shutdown"),
        ("15", "Shell (root)", "shell"),
        ("16", "Jarvis administration shell (cmd2)", "jarvis_shell"),
        ("17", "System resources & hardware support (live)", "resources"),
        ("0", "Log out", "logout"),
    )

    def menu(self) -> str:
        """Print the menu in two columns and return the option typed by the operator."""
        half = (len(self.MENU) + 1) // 2
        left, right = self.MENU[:half], self.MENU[half:]
        for i in range(half):
            a = f"{left[i][0]:>3}) {left[i][1]}"
            b = f"{right[i][0]:>3}) {right[i][1]}" if i < len(right) else ""
            print(f" {a:<40}{b}")
        return input(f"\n{BOLD}Enter an option:{RESET} ").strip()

    # --- Access --------------------------------------------------------------------------------
    def login(self) -> bool:
        """Require a web administrator account (Argon2id) unless ``console.require_login`` is false.

        Allows three attempts, each failure being logged and delayed by 2 s to slow down guessing.

        Returns:
            True once authenticated (``self.user`` is set), False after three failures.
        """
        from argon2 import PasswordHasher
        from argon2.exceptions import VerificationError

        if not self.s.console.require_login:
            return True
        clear()
        print(f"{BOLD}{CYAN}JARVIS appliance console{RESET} – authentication required\n")
        for _ in range(3):
            username = input("Username: ").strip()
            password = getpass.getpass("Password: ")
            user = self.db.get_user_by_name(username)
            try:
                if user is None:
                    raise VerificationError()
                PasswordHasher().verify(user["password_hash"], password)
            except VerificationError:
                self.db.log_event("console_login_failed", actor=username[:60], tty=os.ttyname(0) if sys.stdin.isatty() else "?")
                print(f"{RED}Invalid credentials.{RESET}\n")
                time.sleep(2)
                continue
            self.user = username
            self.db.log_event("console_login", actor=username, tty=os.ttyname(0) if sys.stdin.isatty() else "?")
            return True
        return False

    # --- Actions --------------------------------------------------------------------------------
    def do_status(self) -> None:
        """Refresh the status (nothing to do: the banner is redrawn on every loop iteration)."""
        pass  # the banner is redrawn at every loop

    def do_network(self) -> None:
        """Show addresses, routes and DNS servers, with a hint on how to change them (netplan)."""
        print(run(["ip", "-br", "addr"]))
        print("\nRoutes:\n" + run(["ip", "route"]))
        print("\nDNS:\n" + (run(["resolvectl", "dns"]) or Path("/etc/resolv.conf").read_text(errors="ignore")))
        print(f"\n{DIM}Edit /etc/netplan/*.yaml then run 'netplan apply' (see the installation manual).{RESET}")
        pause()

    def do_password(self) -> None:
        """Create a web administrator, set its password, or restore the factory account admin/admin.

        The factory reset sets admin/admin with a mandatory password change at the next web sign-in and
        closes every open web session of the account.
        """
        from argon2 import PasswordHasher

        print("  1) Set the password of an account (created if needed)")
        print("  2) Factory reset: admin / admin, password change forced at the next sign-in")
        choice = input("Choice [1]: ").strip() or "1"
        if choice == "2":
            if not confirm("Reset the web account 'admin' to the factory password 'admin'?"):
                return
            self.db.add_user("admin", PasswordHasher().hash("admin"), must_change=True)
            user = self.db.get_user_by_name("admin")
            closed = self.db.end_user_sessions(user["id"], "revoked")
            self.db.log_event("admin_factory_reset", actor=self.user, via="console", sessions_closed=closed)
            print(f"{GREEN}Account 'admin' reset to admin/admin; the password must be changed at the next sign-in.{RESET}")
            return pause()
        username = input("Account name [admin]: ").strip() or "admin"
        pw = getpass.getpass("New password (12 characters min.): ")
        if len(pw) < 12 or pw != getpass.getpass("Confirm: "):
            print(f"{RED}Password too short or mismatch: nothing changed.{RESET}")
            return pause()
        self.db.add_user(username, PasswordHasher().hash(pw))
        self.db.log_event("user_password_reset", actor=self.user, username=username, via="console")
        print(f"{GREEN}Account '{username}' saved.{RESET}")
        pause()

    def do_restart(self) -> None:
        """Restart jarvis.target (core and API) after confirmation."""
        if confirm("Restart jarvis-core and jarvis-api?"):
            self.db.log_event("services_restarted", actor=self.user, via="console")
            print(run(["systemctl", "restart", "jarvis.target"], timeout=120) or "Restart requested.")
            time.sleep(3)

    def do_logs(self) -> None:
        """Follow the core and API journals until Ctrl+C."""
        print(f"{DIM}Ctrl+C to return to the menu.{RESET}")
        try:
            subprocess.run(["journalctl", "-u", "jarvis-core", "-u", "jarvis-api", "-n", "40", "-f", "--no-pager"])
        except KeyboardInterrupt:
            pass

    def do_check(self) -> None:
        """Run ``jarvis check-config`` (falls back to ``python -m jarvis`` if not on PATH)."""
        exe = shutil.which("jarvis") or f"{sys.executable} -m jarvis"
        subprocess.run([*exe.split(), "check-config"])
        pause()

    def do_hardware(self) -> None:
        """Test the door sensor, LEDs and, on confirmation, the garage relay.

        jarvis-core owns the devices, so it is stopped for the test and always restarted
        afterwards (``finally``).
        """
        from jarvis.hardware.devices import Hardware

        if run(["systemctl", "is-active", "jarvis-core"]) == "active":
            print(f"{YELLOW}jarvis-core owns the hardware: it is stopped during the test.{RESET}")
            if not confirm("Stop jarvis-core for the test?"):
                return
            run(["systemctl", "stop", "jarvis-core"], timeout=60)
        hw = Hardware.from_config(self.s.hardware)
        try:
            print("Door sensor:", hw.door_state())
            for color in ("green", "red"):
                print(f"{color} LED on for 2 s")
                hw.indicate(color, 2)
                time.sleep(2.5)
            if confirm("Send a pulse to the GARAGE DOOR (the door will move)?"):
                hw.pulse_garage()
                self.db.log_event("garage_pulse", actor=self.user, source="console")
        finally:
            hw.close()
            run(["systemctl", "start", "jarvis-core"], timeout=60)
        pause()

    def do_backup(self) -> None:
        """Optionally trigger jarvis-backup.service, then list the archives."""
        dest = Path("/var/backups/jarvis")
        if confirm("Create a backup now?"):
            run(["systemctl", "start", "jarvis-backup.service"], timeout=600)
        for f in sorted(dest.glob("jarvis-*.tar.gz")):
            print(f"  {f.name}  {f.stat().st_size / 1e6:7.1f} MB")
        pause()

    def do_restore(self) -> None:
        """Restore a chosen archive: stop services, extract, fix ownership, restart, audit.

        Stale WAL/SHM files are deleted so SQLite does not replay them over the restored
        database.
        """
        backups = sorted(Path("/var/backups/jarvis").glob("jarvis-*.tar.gz"))
        if not backups:
            print("No backup found in /var/backups/jarvis.")
            return pause()
        for i, f in enumerate(backups, 1):
            print(f"  {i}) {f.name}")
        choice = input("Backup to restore (number): ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(backups):
            return
        archive = backups[int(choice) - 1]
        if not confirm(f"Replace the CURRENT database and media with {archive.name}?"):
            return
        data = Path(self.s.storage.data_dir)
        run(["systemctl", "stop", "jarvis.target"], timeout=120)
        with tarfile.open(archive) as tar:
            tar.extractall(data, filter="data")  # refuses absolute paths and ".."
        for p in [data / "jarvis.db-wal", data / "jarvis.db-shm"]:
            p.unlink(missing_ok=True)
        run(["chown", "-R", "jarvis:jarvis", str(data)])
        run(["systemctl", "start", "jarvis.target"], timeout=120)
        Database(self.s.storage.db_path).log_event("backup_restored", actor=self.user, archive=archive.name)
        print(f"{GREEN}Restored from {archive.name}.{RESET}")
        pause()

    def do_reset_settings(self) -> None:
        """Delete all web setting overrides (back to config.yaml) and restart jarvis on confirmation."""
        overrides = self.db.get_settings_overrides()
        if not overrides:
            print("No web setting overrides: config.yaml values are in effect.")
            return pause()
        for k, v in overrides.items():
            print(f"  {k} = {v!r}")
        if confirm("Delete these overrides and restart jarvis?"):
            self.db.delete_settings_overrides(list(overrides))
            self.db.log_event("settings_reset", actor=self.user, via="console", keys=list(overrides))
            run(["systemctl", "restart", "jarvis.target"], timeout=120)
        pause()

    def do_verify(self) -> None:
        """Verify the SHA-256 hash chain of the audit log."""
        r = self.db.verify_events()
        if r["ok"]:
            print(f"{GREEN}Audit chain intact{RESET}: {r['checked']} sealed events, last hash {r.get('last_hash')}")
        else:
            print(f"{RED}AUDIT CHAIN BROKEN{RESET} at event #{r['broken_at']} ({r['reason']})")
        pause()

    def do_sessions(self) -> None:
        """List active web sessions and optionally revoke all of them (e.g. after a suspected compromise)."""
        rows = [r for r in self.db.list_sessions(limit=50) if r["ended_at"] is None]
        for r in rows:
            since = datetime.fromtimestamp(r["created_at"] or 0).strftime("%Y-%m-%d %H:%M")
            print(f"  #{r['id']:<5} {r['username']:<16} {r['ip'] or '?':<16} since {since}")
        if rows and confirm("Revoke ALL active web sessions?"):
            now = time.time()
            with self.db.connect() as c:
                c.execute("UPDATE sessions SET ended_at = ?, end_reason = 'revoked' WHERE ended_at IS NULL", (now,))
            self.db.log_event("sessions_revoked_all", actor=self.user, via="console", count=len(rows))
        elif not rows:
            print("No active web session.")
        pause()

    def do_reboot(self) -> None:
        """Reboot the machine after confirmation."""
        if confirm("Reboot the system now?"):
            self.db.log_event("system_reboot", actor=self.user, via="console")
            run(["systemctl", "reboot"])

    def do_shutdown(self) -> None:
        """Power off the machine after confirmation."""
        if confirm("Shut the system down now (the garage will no longer be controlled)?"):
            self.db.log_event("system_shutdown", actor=self.user, via="console")
            run(["systemctl", "poweroff"])

    def do_resources(self) -> None:
        """Live view (refreshed every 2 s) of CPU, RAM, network, disk I/O, GPU and the hardware support."""
        import select

        from jarvis.core.sysinfo import ResourceSampler, detect_capabilities, render_resources

        caps = detect_capabilities()
        sampler = ResourceSampler()
        while True:
            time.sleep(0.2)
            rows = render_resources(sampler.sample(), caps)
            clear()
            print(f"{BOLD}{CYAN}System resources{RESET}  {DIM}{datetime.now():%Y-%m-%d %H:%M:%S} · "
                  f"press Enter to return{RESET}")
            section = None
            for sec, metric, value in rows:
                if sec != section:
                    print(f"\n{BOLD}{sec}{RESET}")
                    section = sec
                print(f"  {metric:<32} {value}")
            ready, _, _ = select.select([sys.stdin], [], [], 2.0)
            if ready:
                sys.stdin.readline()
                return

    def do_jarvis_shell(self) -> None:
        """Open the cmd2 administration shell (see :mod:`jarvis.cli.shell`)."""
        from jarvis.cli.shell import JarvisShell

        self.db.log_event("console_jarvis_shell", actor=self.user)
        JarvisShell(self.s, interactive=True).cmdloop()

    def do_shell(self) -> None:
        """Open a root login shell (audited); exiting it returns to the console."""
        self.db.log_event("console_shell", actor=self.user)
        print(f"{DIM}Type 'exit' to return to the console.{RESET}")
        subprocess.run([os.environ.get("SHELL", "/bin/bash"), "-l"])

    # --- Main loop --------------------------------------------------------------------------------
    def loop(self) -> None:
        """Main loop: log in, then show banner and menu until logout.

        Ctrl+C cancels the current action; EOF (stdin closed) exits. After three failed logins
        the console waits 10 s before prompting again.
        """
        self.user = "console"
        while True:
            try:
                if not self.login():
                    time.sleep(10)
                    continue
            except (KeyboardInterrupt, EOFError):
                if not sys.stdin.isatty():
                    return
                continue
            actions = {key: name for key, _, name in self.MENU}
            while True:
                try:
                    self.banner()
                    choice = self.menu()
                    name = actions.get(choice)
                    if name is None:
                        continue
                    if name == "logout":
                        self.db.log_event("console_logout", actor=self.user)
                        break
                    getattr(self, f"do_{name}")()
                except KeyboardInterrupt:
                    print()
                except EOFError:
                    return


def main(settings: Settings) -> None:
    """Entry point of ``jarvis console``."""
    Console(settings).loop()
