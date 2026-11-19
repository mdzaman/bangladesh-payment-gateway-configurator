"""Accounts, sessions, CSRF, email flows, team membership."""

from sqlalchemy import text

import helpers
from helpers import PASSWORD, Dash, last_email, token_from


def test_signup_creates_user_workspace_and_two_environments(dash, rt):
    assert dash.me["user"]["email"] == dash.email and dash.me["user"]["email_verified"] is False
    ws = dash.me["workspaces"][0]
    assert ws["role"] == "owner" and ws["live_status"] == "inactive"
    with rt.engine.connect() as conn:
        modes = (
            conn.execute(text("SELECT mode FROM workspace_envs WHERE workspace_id = :w ORDER BY mode"), {"w": ws["id"]})
            .scalars()
            .all()
        )
    assert modes == ["live", "sandbox"]
    # The simulator is ready, so a new workspace can take a test payment at once.
    listing = dash.get(f"{dash.env()}/gateways").json()["gateways"]
    assert [g["id"] for g in listing if g["enabled"]] == ["simulator"]


def test_session_cookie_is_host_only_http_only_secure(new_client):
    c = new_client()
    r = Dash(c).signup()
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-bdpay_session=")
    lowered = cookie.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert "domain=" not in lowered
    assert PASSWORD not in r.text and "password_hash" not in r.text


def test_writes_need_the_csrf_token(dash):
    r = dash.c.post("/api/workspaces", json={"name": "No token"})  # cookie only
    assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN"
    r = dash.c.post("/api/workspaces", json={"name": "Wrong token"}, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403
    assert dash.post("/api/workspaces", {"name": "Second shop"}).status_code == 200
    assert dash.c.get("/api/auth/me").status_code == 200  # reads do not need it


def test_anonymous_requests_are_rejected(client):
    for path in ("/api/auth/me", "/api/workspaces", "/api/workspaces/nope/sandbox/orders"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "UNAUTHENTICATED"


def test_login_logout_and_wrong_password(dash, new_client):
    other = Dash(new_client())
    assert other.login(dash.email, "wrong password entirely").status_code == 401
    assert other.login("nobody@example.com").json()["detail"]["message"] == "Invalid email or password"
    assert other.login(dash.email).status_code == 200
    assert other.get("/api/auth/me").json()["user"]["email"] == dash.email
    assert other.post("/api/auth/logout").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert dash.get("/api/auth/me").status_code == 200  # the other session is untouched


def test_account_locks_after_repeated_failures(dash, new_client, app, rt):
    for _ in range(8):
        app.state.limiter.reset()  # isolate the account lock from the rate limit
        assert Dash(new_client()).login(dash.email, "definitely wrong").status_code == 401
    app.state.limiter.reset()
    r = Dash(new_client()).login(dash.email)  # even the right password now
    assert r.status_code == 429 and "Too many failed attempts" in r.json()["detail"]["message"]
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e"), {"e": dash.email}
        )
    assert Dash(new_client()).login(dash.email).status_code == 200


def test_login_is_rate_limited_per_email(new_client, app):
    statuses = [Dash(new_client()).login("target@example.com", "x").status_code for _ in range(12)]
    assert statuses[:10] == [401] * 10 and statuses[10:] == [429, 429]


def test_password_policy(new_client):
    for bad in ("short", "password123", "aaaaaaaaaaaa"):
        r = new_client().post(
            "/api/auth/signup", json={"email": f"p-{helpers.rand_b32(6)}@example.com", "password": bad, "name": "A B"}
        )
        assert r.status_code == 400, bad
    r = new_client().post(
        "/api/auth/signup", json={"email": "samesame@example.com", "password": "samesame@example.com", "name": "A B"}
    )
    assert r.status_code == 400


def test_duplicate_email(dash, new_client):
    r = Dash(new_client()).signup(email=dash.email.upper())  # case-insensitive
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE"


def test_verify_email(dash, rt, new_client):
    mail = last_email(rt, dash.email)
    assert "Confirm your email" in mail["subject"]
    token = token_from(mail)
    assert new_client().post("/api/auth/verify-email", json={"token": "0" * 48}).status_code == 400
    assert new_client().post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert dash.get("/api/auth/me").json()["user"]["email_verified"] is True
    assert new_client().post("/api/auth/verify-email", json={"token": token}).status_code == 400  # single use


def test_password_reset_revokes_sessions_and_does_not_reveal_accounts(dash, rt, new_client):
    anon = new_client()
    assert anon.post("/api/auth/forgot-password", json={"email": "ghost@example.com"}).json() == {"ok": True}
    assert last_email(rt, "ghost@example.com") is None
    assert anon.post("/api/auth/forgot-password", json={"email": dash.email}).json() == {"ok": True}
    token = token_from(last_email(rt, dash.email))
    new_password = "a brand new long password"
    assert anon.post("/api/auth/reset-password", json={"token": token, "password": "short"}).status_code == 400
    # A failed attempt must not burn the link.
    assert anon.post("/api/auth/reset-password", json={"token": token, "password": new_password}).status_code == 200
    assert dash.get("/api/auth/me").status_code == 401  # old sessions are gone
    assert Dash(new_client()).login(dash.email).status_code == 401
    assert Dash(new_client()).login(dash.email, new_password).status_code == 200


def test_change_password_keeps_this_session_only(dash, new_client):
    second = Dash(new_client())
    second.login(dash.email)
    r = dash.post("/api/auth/change-password", {"current_password": "wrong", "new_password": "another long password 1"})
    assert r.status_code == 400
    r = dash.post(
        "/api/auth/change-password", {"current_password": PASSWORD, "new_password": "another long password 1"}
    )
    assert r.status_code == 200
    assert dash.get("/api/auth/me").status_code == 200 and second.get("/api/auth/me").status_code == 401


def test_invitation_for_a_new_person(dash, rt, new_client):
    r = dash.post(f"/api/workspaces/{dash.slug}/invitations", {"email": "new.dev@example.com", "role": "developer"})
    assert r.status_code == 200, r.text
    token = token_from(last_email(rt, "new.dev@example.com"))
    anon = new_client()
    preview = anon.get(f"/api/auth/invitation?token={token}").json()
    assert (
        preview["role"] == "developer" and preview["has_account"] is False and preview["email"] == "new.dev@example.com"
    )
    # The invitation is for that address only.
    wrong = Dash(new_client()).signup(email="someone.else@example.com", invite_token=token)
    assert wrong.status_code == 403
    joined = Dash(anon)
    r = joined.signup(email="new.dev@example.com", invite_token=token)
    assert r.status_code == 200, r.text
    assert [(w["slug"], w["role"]) for w in joined.me["workspaces"]] == [(dash.slug, "developer")]
    assert joined.me["user"]["email_verified"] is True  # the link proved the address
    members = dash.get(f"/api/workspaces/{dash.slug}/members").json()
    assert {m["email"]: m["role"] for m in members["members"]} == {
        dash.email: "owner",
        "new.dev@example.com": "developer",
    }
    assert members["invitations"] == []


def test_invitation_for_an_existing_account_and_role_rules(dash, rt, new_client):
    other = Dash(new_client())
    other.signup()
    dash.post(f"/api/workspaces/{dash.slug}/invitations", {"email": other.email, "role": "admin"})
    token = token_from(last_email(rt, other.email))
    r = other.post("/api/auth/accept-invitation", {"token": token})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    base = f"/api/workspaces/{dash.slug}"
    owner_id = dash.me["user"]["id"]
    # An admin cannot make owners, demote the owner, or remove the owner.
    assert other.post(f"{base}/invitations", {"email": "x@example.com", "role": "owner"}).status_code == 403
    assert other.patch(f"{base}/members/{owner_id}", {"role": "analyst"}).status_code == 403
    assert other.delete(f"{base}/members/{owner_id}").status_code == 403
    # The last owner cannot leave or step down.
    assert dash.delete(f"{base}/members/{owner_id}").status_code == 400
    assert dash.patch(f"{base}/members/{owner_id}", {"role": "admin"}).status_code == 400
    assert dash.patch(f"{base}/members/{other.me['user']['id']}", {"role": "analyst"}).json()["role"] == "analyst"
    assert other.get(f"{base}/audit-log").status_code == 403  # analysts cannot read the audit log
    actions = [e["action"] for e in dash.get(f"{base}/audit-log").json()["data"]]
    assert {"member.invited", "member.joined", "member.role_changed"} <= set(actions)
    assert other.delete(f"{base}/members/{other.me['user']['id']}").status_code == 200  # anyone may leave
    assert other.get(base).status_code == 404


def test_signup_can_be_closed(rt, new_client, dash):
    from fastapi.testclient import TestClient

    from bdpay_cloud.api.app import create_app

    from conftest import make_settings

    closed = create_app(make_settings(allow_signup=False))
    with TestClient(closed, base_url="https://pay.test") as c:
        r = Dash(c).signup()
        assert r.status_code == 403 and "invitation" in r.json()["detail"]["message"]
        # An invitation still works.
        dash.post(f"/api/workspaces/{dash.slug}/invitations", {"email": "invited.only@example.com", "role": "analyst"})
        token = token_from(last_email(rt, "invited.only@example.com"))
        assert Dash(c).signup(email="invited.only@example.com", invite_token=token).status_code == 200
    closed.state.rt.close()


def test_workspaces_are_invisible_to_non_members(dash, new_client):
    stranger = Dash(new_client())
    stranger.signup()
    for path in (
        f"/api/workspaces/{dash.slug}",
        f"/api/workspaces/{dash.slug}/members",
        f"{dash.env()}/orders",
        f"{dash.env()}/gateways",
        f"{dash.env()}/api-keys",
    ):
        r = stranger.get(path)
        assert r.status_code == 404, path  # not 403: do not confirm it exists
    assert stranger.get("/api/workspaces/does-not-exist").status_code == 404


def test_go_live_needs_owner_verified_email_and_the_attestation(dash, rt, new_client):
    base = f"/api/workspaces/{dash.slug}"
    assert dash.get(f"{dash.env('live')}/gateways").status_code == 403
    assert dash.get(f"{dash.env('live')}/gateways").json()["detail"]["code"] == "LIVE_NOT_ACTIVE"
    body = {"accept": True, "legal_name": "Rahim Store Ltd"}
    assert dash.post(f"{base}/go-live", body).status_code == 403  # email not confirmed yet
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE users SET email_verified_at = now() WHERE email = :e"), {"e": dash.email})
    assert dash.post(f"{base}/go-live", {**body, "accept": False}).status_code == 400
    r = dash.post(f"{base}/go-live", body)
    assert r.status_code == 200 and r.json()["live_status"] == "active"
    assert dash.get(f"{dash.env('live')}/gateways").status_code == 200
    with rt.engine.connect() as conn:
        attestation = conn.execute(
            text("SELECT live_attestation FROM workspaces WHERE id = :w"), {"w": dash.workspace_id}
        ).scalar()
    assert attestation["legal_name"] == "Rahim Store Ltd" and attestation["email"] == dash.email
    assert "never receives, holds or moves the money" in attestation["text"]
    # The simulator does not exist in live mode.
    assert "simulator" not in [g["id"] for g in dash.get(f"{dash.env('live')}/gateways").json()["gateways"]]


def test_profile_and_sessions(dash, new_client):
    assert dash.patch("/api/auth/profile", {"name": "  Rahim   Khan "}).json()["name"] == "Rahim Khan"
    second = Dash(new_client())
    second.login(dash.email)
    assert len(dash.get("/api/auth/sessions").json()["data"]) == 2
    assert dash.post("/api/auth/sessions/revoke-others").status_code == 200
    assert second.get("/api/auth/me").status_code == 401 and dash.get("/api/auth/me").status_code == 200
