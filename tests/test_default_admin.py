# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_default_admin.py
# Purpose : Factory admin/admin account and mandatory password change at first sign-in
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""The factory account admin/admin exists only on an empty database, can do nothing but change its
password, and the change enforces the password policy, revokes the other sessions and is audited."""

import pytest
from fastapi.testclient import TestClient

from jarvis.config.settings import Settings
from jarvis.storage.database import Database
from jarvis.web.app import create_app

H = {"X-Jarvis": "1"}


class Core:
    def call(self, cmd, **k):
        return {"ok": True}


@pytest.fixture
def app_db(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    return create_app(s, db, Core()), db


def sign_in(app, password="admin"):
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "admin", "password": password}, headers=H)
    return c, r


def test_factory_account_forces_password_change(app_db):
    app, db = app_db
    assert db.list_events(type_="default_admin_created")
    c, r = sign_in(app)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert c.get("/api/me").json()["must_change_password"] is True
    assert c.get("/api/persons").status_code == 403                    # everything else is blocked
    assert c.get("/api/persons").json()["detail"] == "Password change required"
    weak = [("admin", "admin"), ("admin", "short"), ("admin", "aaaaaaaaaaaaaaaa"), ("wrong", "Correct-Horse-9")]
    for current, new in weak:
        r = c.post("/api/account/password", json={"current_password": current, "new_password": new}, headers=H)
        assert r.status_code in (401, 422)
    other, _ = sign_in(app)                                             # a second session of the same account
    r = c.post("/api/account/password", json={"current_password": "admin", "new_password": "Correct-Horse-9"}, headers=H)
    assert r.status_code == 200 and r.json()["other_sessions_revoked"] == 1
    assert c.get("/api/persons").status_code == 200                      # unlocked
    assert other.get("/api/me").status_code == 401                       # other session revoked
    assert sign_in(app)[1].status_code == 401                            # admin/admin no longer works
    assert sign_in(app, "Correct-Horse-9")[1].json()["must_change_password"] is False
    ev = db.list_events(type_="password_changed")[0]
    assert ev["actor"] == "admin" and ev["details"]["forced"] is True


def test_no_default_account_when_users_exist(tmp_path):
    from argon2 import PasswordHasher

    s = Settings()
    s.storage.data_dir = str(tmp_path)
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("owner", PasswordHasher().hash("a-long-password-1"))
    create_app(s, db, Core())
    assert db.get_user_by_name("admin") is None


def test_factory_reset_restores_admin_admin(app_db):
    from argon2 import PasswordHasher

    app, db = app_db
    user = db.get_user_by_name("admin")
    db.set_password(user["id"], PasswordHasher().hash("Some-Other-Pass-7"))
    db.add_user("admin", PasswordHasher().hash("admin"), must_change=True)   # what the console reset does
    c, r = sign_in(app)
    assert r.json()["must_change_password"] is True and c.get("/api/persons").status_code == 403
