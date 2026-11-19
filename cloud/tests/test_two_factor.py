"""Two-factor sign-in: one-time codes, recovery codes, the sign-in challenge, lock-out."""

import base64
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from argon2 import PasswordHasher
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from bdpay_cloud import migrate
from bdpay_cloud.crypto import Vault, aad_totp
from bdpay_cloud.db import session_scope, utcnow
from bdpay_cloud.models import User
from bdpay_cloud.security import passwords, totp
from bdpay_cloud.services import auth
from bdpay_cloud.workers import housekeeping

from conftest import TEST_KEYS
from helpers import PASSWORD, Dash, last_email, run_cli, token_from


@pytest.fixture
def clock(monkeypatch):
    """The clock one-time codes are made from, moved by hand: a code works once,
    so a test that signs in twice needs time to pass in between."""

    class Clock:
        def __init__(self):
            self.now = time.time()

        def tick(self, seconds: float = totp.PERIOD) -> None:
            self.now += seconds

    c = Clock()
    monkeypatch.setattr(totp, "unix_time", lambda: c.now)
    return c


def code_for(secret: str, offset: int = 0) -> str:
    return totp.code_at(secret, totp.current_step() + offset)


def wrong_code(secret: str) -> str:
    valid = {code_for(secret, o) for o in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


def enrol(dash) -> tuple[str, list[str]]:
    """Switch two-factor sign-in on. Returns (secret, recovery codes)."""
    r = dash.post("/api/auth/two-factor/setup", {"current_password": PASSWORD})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = dash.post("/api/auth/two-factor/enable", {"code": code_for(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def start(client, email: str, password: str = PASSWORD) -> str:
    """Step one of sign-in. Returns the challenge; there is no session yet."""
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["two_factor_required"] is True and body["expires_in"] == 600
    assert "csrf_token" not in body and "user" not in body and "set-cookie" not in r.headers
    return body["challenge"]


def finish(client, challenge: str, code: str):
    return client.post("/api/auth/login/two-factor", json={"challenge": challenge, "code": code})


def user_row(rt, email: str):
    with rt.engine.connect() as conn:
        return conn.execute(text("SELECT * FROM users WHERE email = :e"), {"e": email}).one()


# ------------------------------------------------------------------- the codes
def test_totp_matches_rfc_6238():
    secret = base64.b32encode(b"12345678901234567890").decode()  # appendix B, SHA-1 rows
    vectors = {
        59: "94287082",
        1111111109: "07081804",
        1111111111: "14050471",
        1234567890: "89005924",
        2000000000: "69279037",
        20000000000: "65353130",
    }
    for at, expected in vectors.items():
        assert totp.code_at(secret, at // 30, digits=8) == expected
    assert totp.code_at(secret, 59 // 30) == "287082"


def test_totp_accepts_one_step_either_side_and_each_code_once():
    secret, at = totp.new_secret(), 1_700_000_015.0
    step = totp.current_step(at)
    assert len(secret) == 32 and len(base64.b32decode(secret)) == 20
    for offset in (-1, 0, 1):
        assert totp.verify(secret, totp.code_at(secret, step + offset), at=at) == step + offset
    for offset in (-2, 2):  # outside the window (a collision with a valid code is a 3 in a million accident)
        candidate = totp.code_at(secret, step + offset)
        if candidate not in {totp.code_at(secret, step + o) for o in (-1, 0, 1)}:
            assert totp.verify(secret, candidate, at=at) is None
    code = totp.code_at(secret, step)
    assert totp.verify(secret, code, last_step=step, at=at) is None  # used already
    assert totp.verify(secret, code, last_step=step + 1, at=at) is None  # an older step than the last accepted
    assert totp.verify(secret, totp.code_at(secret, step + 1), last_step=step, at=at) == step + 1
    # What people really type: a space in the middle, Bangla digits.
    assert totp.verify(secret, f"{code[:3]} {code[3:]}", at=at) == step
    assert totp.verify(secret, code.translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")), at=at) == step
    for junk in ("", "12345", "1234567", "abcdef", "12345a", code + "0"):
        assert totp.verify(secret, junk, at=at) is None


def test_provisioning_link_and_recovery_code_shape():
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "rahim+shop@example.com", "bdpay cloud")
    assert uri == (
        "otpauth://totp/bdpay%20cloud:rahim%2Bshop%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=bdpay%20cloud"
    )
    codes = totp.new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(re.fullmatch(r"[0-9a-hjkmnp-tv-z]{4}(-[0-9a-hjkmnp-tv-z]{4}){3}", c) for c in codes)
    # However it is typed back, it is the same code; another account's hash differs.
    spoken = codes[0].upper().replace("-", " ").replace("0", "O").replace("1", "l")
    assert totp.recovery_hash("usr_a", spoken) == totp.recovery_hash("usr_a", codes[0])
    assert totp.recovery_hash("usr_b", codes[0]) != totp.recovery_hash("usr_a", codes[0])


# ------------------------------------------------------------------ switching on
def test_set_up_and_confirm(dash, new_client, rt, clock):
    assert dash.me["user"]["two_factor_enabled"] is False
    assert dash.get("/api/auth/two-factor").json() == {"enabled": False, "enabled_at": None, "recovery_codes_left": 0}
    assert dash.c.post("/api/auth/two-factor/setup", json={"current_password": PASSWORD}).status_code == 403  # CSRF
    assert dash.post("/api/auth/two-factor/setup", {"current_password": "not my password"}).status_code == 400
    r = dash.post("/api/auth/two-factor/enable", {"code": "123456"})  # nothing to confirm yet
    assert r.status_code == 400 and r.json()["detail"]["code"] == "SETUP_EXPIRED"
    other = Dash(new_client())
    assert other.login(dash.email).status_code == 200

    made = dash.post("/api/auth/two-factor/setup", {"current_password": PASSWORD}).json()
    secret = made["secret"]
    assert re.fullmatch(r"[A-Z2-7]{32}", secret) and made["expires_in"] == 900
    assert made["otpauth_uri"].startswith("otpauth://totp/bdpay%20cloud:") and f"secret={secret}" in made["otpauth_uri"]
    # Not on yet: signing in still takes the password alone, and nothing is asked for.
    assert Dash(new_client()).login(dash.email).json()["user"]["two_factor_enabled"] is False
    assert dash.get("/api/auth/two-factor").json()["enabled"] is False

    r = dash.post("/api/auth/two-factor/enable", {"code": wrong_code(secret)})
    assert r.status_code == 400 and "not correct" in r.json()["detail"]["message"]
    r = dash.post("/api/auth/two-factor/enable", {"code": code_for(secret)})
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10
    status = dash.get("/api/auth/two-factor").json()
    assert status["enabled"] is True and status["enabled_at"] and status["recovery_codes_left"] == 10
    assert dash.get("/api/auth/me").json()["user"]["two_factor_enabled"] is True
    assert other.get("/api/auth/me").status_code == 401  # every other device is signed out

    # At rest: the secret is ciphertext, the recovery codes are hashes.
    row = user_row(rt, dash.email)
    assert row.totp_secret_enc.startswith("v1.t1.") and secret not in row.totp_secret_enc
    assert rt.vault.decrypt_text(row.totp_secret_enc, aad_totp(row.id)) == secret
    with rt.engine.connect() as conn:
        stored = conn.execute(text("SELECT id FROM recovery_codes WHERE user_id = :u"), {"u": row.id}).scalars().all()
        actions = (
            conn.execute(text("SELECT action FROM audit_log WHERE actor_id = :u ORDER BY id"), {"u": row.id})
            .scalars()
            .all()
        )
    assert len(stored) == 10 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in stored)
    assert not {c.replace("-", "") for c in codes} & set(stored)
    assert "user.two_factor_enabled" in actions
    assert "two-factor sign-in was turned on" in last_email(rt, dash.email)["subject"]

    # Setting it up again means switching it off first.
    r = dash.post("/api/auth/two-factor/setup", {"current_password": PASSWORD})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DUPLICATE"
    assert dash.post("/api/auth/two-factor/enable", {"code": code_for(secret, 1)}).status_code == 409


def test_an_unfinished_set_up_expires(dash, rt, clock):
    secret = dash.post("/api/auth/two-factor/setup", {"current_password": PASSWORD}).json()["secret"]
    assert user_row(rt, dash.email).totp_started_at is not None
    with rt.engine.begin() as conn:  # sixteen minutes go by
        conn.execute(
            text("UPDATE users SET totp_started_at = now() - interval '16 minutes' WHERE email = :e"), {"e": dash.email}
        )
    r = dash.post("/api/auth/two-factor/enable", {"code": code_for(secret)})  # the right code, too late
    assert r.status_code == 400 and r.json()["detail"]["code"] == "SETUP_EXPIRED"
    assert "expired" in r.json()["detail"]["message"]
    row = user_row(rt, dash.email)
    assert row.totp_enabled_at is None and row.failed_logins == 0  # late is not a wrong guess
    assert dash.get("/api/auth/two-factor").json()["enabled"] is False

    # Starting again makes a new secret, and only that one can be confirmed.
    again = dash.post("/api/auth/two-factor/setup", {"current_password": PASSWORD}).json()["secret"]
    assert again != secret
    if code_for(secret) not in {code_for(again, o) for o in (-1, 0, 1)}:
        assert dash.post("/api/auth/two-factor/enable", {"code": code_for(secret)}).status_code == 400
    assert dash.post("/api/auth/two-factor/enable", {"code": code_for(again)}).status_code == 200
    row = user_row(rt, dash.email)
    assert row.totp_enabled_at is not None and row.totp_started_at is None


def test_housekeeping_forgets_abandoned_set_ups(dash, new_client, rt, clock):
    fresh, on = Dash(new_client()), Dash(new_client())
    fresh.signup()
    on.signup()
    for who in (dash, fresh):
        assert who.post("/api/auth/two-factor/setup", {"current_password": PASSWORD}).status_code == 200
    enrol(on)
    with rt.engine.begin() as conn:  # dash walked away two days ago
        conn.execute(
            text("UPDATE users SET totp_started_at = now() - interval '2 days' WHERE email = :e"), {"e": dash.email}
        )
    assert housekeeping.run(rt)["two_factor_setups"] >= 1
    gone = user_row(rt, dash.email)
    assert gone.totp_secret_enc is None and gone.totp_started_at is None
    # A set-up still in progress, and a factor that is on, are left alone.
    assert user_row(rt, fresh.email).totp_secret_enc is not None
    assert user_row(rt, on.email).totp_secret_enc is not None
    assert housekeeping.run(rt)["two_factor_setups"] == 0
    r = dash.post("/api/auth/two-factor/enable", {"code": "123456"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "SETUP_EXPIRED"


def test_secrets_never_leave_after_set_up(dash, clock):
    secret, codes = enrol(dash)
    for path in ("/api/auth/me", "/api/auth/two-factor", "/api/auth/sessions", f"/api/workspaces/{dash.slug}/members"):
        body = dash.get(path).text
        assert secret not in body and codes[0] not in body and "totp" not in body, path


# -------------------------------------------------------------------- signing in
def test_sign_in_takes_the_password_and_then_a_code(dash, new_client, clock):
    secret, _ = enrol(dash)
    clock.tick()
    c = new_client()
    challenge = start(c, dash.email)
    assert c.get("/api/auth/me").status_code == 401  # the password alone opens nothing
    r = finish(c, challenge, wrong_code(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "UNAUTHENTICATED"
    assert "not correct" in r.json()["detail"]["message"] and "set-cookie" not in r.headers
    assert c.get("/api/auth/me").status_code == 401
    r = finish(c, challenge, code_for(secret))  # a slip does not mean starting over
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == dash.email and r.json()["csrf_token"]
    assert r.headers["set-cookie"].startswith("__Host-bdpay_session=")
    assert c.get("/api/auth/me").json()["user"]["two_factor_enabled"] is True
    # The challenge is spent, and a wrong password never gets one.
    clock.tick()
    r = finish(new_client(), challenge, code_for(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    r = new_client().post("/api/auth/login", json={"email": dash.email, "password": "the wrong password"})
    assert r.status_code == 401 and r.json()["detail"]["message"] == "Invalid email or password"


def test_a_code_cannot_be_replayed(dash, new_client, clock):
    secret, _ = enrol(dash)
    # The code that confirmed the set-up is spent too.
    assert finish(new_client(), start(new_client(), dash.email), code_for(secret)).status_code == 401
    clock.tick()
    code = code_for(secret)
    assert finish(new_client(), start(new_client(), dash.email), code).status_code == 200
    assert finish(new_client(), start(new_client(), dash.email), code).status_code == 401  # seen once already
    assert finish(new_client(), start(new_client(), dash.email), code_for(secret, -1)).status_code == 401  # older
    clock.tick()
    assert finish(new_client(), start(new_client(), dash.email), code_for(secret)).status_code == 200


def test_two_requests_with_the_same_code_get_one_session(dash, new_client, rt, clock):
    secret, codes = enrol(dash)
    clock.tick()
    for code in (code_for(secret), codes[0]):  # a code from the app, then a recovery code
        attempts = [(c, start(c, dash.email)) for c in (new_client(), new_client(), new_client(), new_client())]
        barrier = threading.Barrier(len(attempts))

        def go(attempt, code=code, barrier=barrier):
            barrier.wait()
            return finish(*attempt, code).status_code

        with ThreadPoolExecutor(len(attempts)) as pool:
            statuses = sorted(pool.map(go, attempts))
        assert statuses == [200, 401, 401, 401], code
        with rt.engine.begin() as conn:  # three refusals are three failed attempts: the next round starts clean
            conn.execute(text("UPDATE users SET failed_logins = 0 WHERE email = :e"), {"e": dash.email})
    assert dash.get("/api/auth/two-factor").json()["recovery_codes_left"] == 9


def test_recovery_code_works_once(dash, new_client, rt, clock):
    _, codes = enrol(dash)
    other = Dash(new_client())
    other.signup()
    _, other_codes = enrol(other)
    typed = codes[0].upper().replace("-", " ")  # as someone reads it off paper
    r = finish(new_client(), start(new_client(), dash.email), typed)
    assert r.status_code == 200, r.text
    assert dash.get("/api/auth/two-factor").json()["recovery_codes_left"] == 9
    assert finish(new_client(), start(new_client(), dash.email), codes[0]).status_code == 401  # spent
    assert finish(new_client(), start(new_client(), dash.email), other_codes[0]).status_code == 401  # someone else's
    assert finish(new_client(), start(new_client(), dash.email), codes[1]).status_code == 200
    assert dash.get("/api/auth/two-factor").json()["recovery_codes_left"] == 8
    assert other.get("/api/auth/two-factor").json()["recovery_codes_left"] == 10
    # It is recorded, and the account holder is told.
    with rt.engine.connect() as conn:
        left = (
            conn.execute(
                text(
                    "SELECT detail->>'left' FROM audit_log WHERE actor_id = :u AND action = 'user.recovery_code_used' "
                    "ORDER BY id"
                ),
                {"u": dash.me["user"]["id"]},
            )
            .scalars()
            .all()
        )
    assert left == ["9", "8"]
    assert "a recovery code was used to sign in" in last_email(rt, dash.email)["subject"]


def test_wrong_codes_lock_the_account_and_a_right_password_does_not_reset_the_count(dash, new_client, app, rt, clock):
    secret, codes = enrol(dash)
    clock.tick()
    bad = wrong_code(secret)
    for _ in range(4):
        # A new challenge each time: every one of these is a correct password.
        app.state.limiter.reset()  # isolate the account lock from the rate limits
        assert finish(new_client(), start(new_client(), dash.email), bad).status_code == 401
    assert user_row(rt, dash.email).failed_logins == 4
    app.state.limiter.reset()
    challenge = start(new_client(), dash.email)
    assert user_row(rt, dash.email).failed_logins == 4  # the password was right; the count stays
    for _ in range(3):
        assert finish(new_client(), challenge, "not-a-recovery-code").status_code == 401
    assert finish(new_client(), challenge, bad).status_code == 401  # the eighth
    locked = user_row(rt, dash.email)
    assert locked.locked_until is not None and locked.failed_logins == 0
    # Locked: not even the right code, a recovery code, or the right password gets anywhere.
    for attempt in (code_for(secret), codes[0]):
        r = finish(new_client(), challenge, attempt)
        assert r.status_code == 429 and "Too many failed attempts" in r.json()["detail"]["message"]
    assert new_client().post("/api/auth/login", json={"email": dash.email, "password": PASSWORD}).status_code == 429
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e"), {"e": dash.email}
        )
    assert finish(new_client(), challenge, code_for(secret)).status_code == 200
    assert dash.get("/api/auth/two-factor").json()["recovery_codes_left"] == 10  # the refused one was not used up


def test_failures_at_the_same_moment_are_each_counted(dash, new_client, app, rt):
    # Two requests that have both read the account before either has failed.
    a, b = rt.sessions(), rt.sessions()
    try:
        first = a.scalar(select(User).where(User.email == dash.email))
        second = b.scalar(select(User).where(User.email == dash.email))
        assert first.failed_logins == second.failed_logins == 0
        auth._count_failure(a, first, utcnow())
        auth._count_failure(b, second, utcnow())
    finally:
        a.close()
        b.close()
    assert user_row(rt, dash.email).failed_logins == 2

    # And through the front door: eight wrong passwords sent at once lock the account.
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE users SET failed_logins = 0 WHERE email = :e"), {"e": dash.email})
    clients = [new_client() for _ in range(8)]
    app.state.limiter.reset()
    barrier = threading.Barrier(len(clients))

    def guess(c):
        barrier.wait()
        return c.post("/api/auth/login", json={"email": dash.email, "password": "definitely wrong"}).status_code

    with ThreadPoolExecutor(len(clients)) as pool:
        statuses = list(pool.map(guess, clients))
    assert statuses == [401] * 8
    row = user_row(rt, dash.email)
    assert row.locked_until is not None and row.failed_logins == 0


@pytest.mark.parametrize("meanwhile", ["locked", "password changed"])
def test_a_right_password_is_checked_again_after_the_slow_part(dash, new_client, rt, monkeypatch, meanwhile):
    """Checking a password takes a while and the account is not locked for it.
    What happened in that time still counts."""
    real = passwords.verify_password
    change = (
        "locked_until = now() + interval '15 minutes'"
        if meanwhile == "locked"
        else "password_hash = 'changed-meanwhile'"
    )

    def slow(stored, given):
        ok = real(stored, given)
        if ok:  # while this one was being checked, other requests got there first
            with rt.engine.begin() as conn:
                conn.execute(text(f"UPDATE users SET {change} WHERE email = :e"), {"e": dash.email})
        return ok

    monkeypatch.setattr(passwords, "verify_password", slow)
    c = new_client()
    r = c.post("/api/auth/login", json={"email": dash.email, "password": PASSWORD})
    assert r.status_code == (429 if meanwhile == "locked" else 401) and "set-cookie" not in r.headers
    assert c.get("/api/auth/me").status_code == 401


def test_two_right_sign_ins_while_the_password_is_being_re_hashed(dash, new_client, rt, monkeypatch):
    # The stored hash was made with older settings, so the next sign-in re-hashes it.
    old = PasswordHasher(time_cost=2, memory_cost=8, parallelism=1).hash(PASSWORD)
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE users SET password_hash = :h WHERE email = :e"), {"h": old, "e": dash.email})
    assert passwords.needs_rehash(old)
    both_checked = threading.Barrier(2, timeout=5)
    real = passwords.verify_password

    def verify_then_meet(stored, given):
        ok = real(stored, given)
        if ok and stored == old:
            both_checked.wait()  # both have checked the password before either takes the account row
        return ok

    monkeypatch.setattr(passwords, "verify_password", verify_then_meet)

    def sign_in(c):
        return c.post("/api/auth/login", json={"email": dash.email, "password": PASSWORD}).status_code

    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(sign_in, [new_client(), new_client()]))
    # The second finds a different hash from the one it checked. It is the same password: both are let in.
    assert statuses == [200, 200]
    assert not passwords.needs_rehash(user_row(rt, dash.email).password_hash)


def test_holding_an_account_does_not_hold_up_what_only_refers_to_it(dash, rt):
    """Sign-in, the account page and a password reset lock the account row. A new
    emailed link or a new session for that account only refers to the row, and
    must not have to wait for it."""
    uid = dash.me["user"]["id"]
    # What sign-in and the account page take, then what a password reset takes.
    for take in (auth.lock_account, auth.revoke_sessions):
        holder = rt.sessions()
        try:
            take(holder, uid)
            with rt.engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '2s'"))
                conn.execute(
                    text(
                        "INSERT INTO email_tokens (id, user_id, purpose, expires_at) "
                        "VALUES (:i, :u, 'verify_email', now())"
                    ),
                    {"i": "probe-" + uuid.uuid4().hex, "u": uid},
                )
            # Another change to the same account still waits its turn.
            with pytest.raises(OperationalError), rt.engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
                conn.execute(text("UPDATE users SET name = name WHERE id = :u"), {"u": uid})
        finally:
            holder.rollback()
            holder.close()
    assert dash.get("/api/auth/me").status_code == 200  # the holders rolled back: nothing was revoked


def test_a_reset_and_another_forgot_password_at_the_same_moment(dash, rt):
    with session_scope(rt.sessions) as db:
        auth.request_password_reset(db, rt.settings, dash.email)
    token = token_from(last_email(rt, dash.email))
    reset_has_the_account, forgot_has_the_link = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}

    def use_the_link():
        try:
            with session_scope(rt.sessions) as db:
                auth.reset_password(db, token, "a brand new long password")  # takes the account row
                reset_has_the_account.set()
                forgot_has_the_link.wait(5)
                time.sleep(0.4)  # the other request reaches its commit
            outcome["reset"] = "committed"  # leaving the block writes used_at on the link
        except DBAPIError as e:
            outcome["reset"] = type(e.orig).__name__

    def ask_for_another():
        try:
            reset_has_the_account.wait(5)
            with session_scope(rt.sessions) as db:
                auth.request_password_reset(db, rt.settings, dash.email)  # marks the open link used: locks its row
                forgot_has_the_link.set()
            outcome["forgot"] = "committed"  # leaving the block inserts the new link, which refers to the account
        except DBAPIError as e:
            outcome["forgot"] = type(e.orig).__name__

    threads = [threading.Thread(target=use_the_link), threading.Thread(target=ask_for_another)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert outcome == {"reset": "committed", "forgot": "committed"}


def test_wrong_guesses_on_the_account_page_count_towards_the_lock(dash, new_client, app, rt, clock):
    """A session someone else got hold of is not a free place to guess the password or a code."""
    setup, enable = "/api/auth/two-factor/setup", "/api/auth/two-factor/enable"
    off, renew, change = (
        "/api/auth/two-factor/disable",
        "/api/auth/two-factor/recovery-codes",
        "/api/auth/change-password",
    )
    new_password = {"current_password": PASSWORD, "new_password": "another long password 1"}

    def count() -> int:
        return user_row(rt, dash.email).failed_logins

    assert dash.post(setup, {"current_password": "not my password"}).status_code == 400 and count() == 1
    assert dash.post(change, new_password | {"current_password": "not my password"}).status_code == 400 and count() == 2
    secret = dash.post(setup, {"current_password": PASSWORD}).json()["secret"]
    assert dash.post(enable, {"code": wrong_code(secret)}).status_code == 400 and count() == 3
    assert dash.post(enable, {"code": code_for(secret)}).status_code == 200
    assert count() == 3  # getting it right on this page does not wipe the count; only signing in does
    clock.tick()
    good = {"current_password": PASSWORD, "code": code_for(secret)}
    assert dash.post(off, good | {"current_password": "not my password"}).status_code == 400 and count() == 4
    assert dash.post(off, good | {"code": wrong_code(secret)}).status_code == 400 and count() == 5
    assert dash.post(renew, good | {"current_password": "not my password"}).status_code == 400 and count() == 6
    assert dash.post(renew, good | {"code": "not-a-recovery-code"}).status_code == 400 and count() == 7
    app.state.limiter.reset()  # what follows is the account's own lock, not the rate limiter
    assert dash.post(off, good | {"code": wrong_code(secret)}).status_code == 400  # the eighth
    locked = user_row(rt, dash.email)
    assert locked.locked_until is not None and locked.failed_logins == 0

    # Locked: the right password and the right code get nowhere, on any of these pages or at sign-in.
    attempts = (
        (off, good),
        (renew, good),
        (change, new_password),
        (setup, {"current_password": PASSWORD}),
        (enable, {"code": code_for(secret)}),
    )
    for path, body in attempts:
        r = dash.post(path, body)
        assert r.status_code == 429 and "Too many failed attempts" in r.json()["detail"]["message"], path
    assert new_client().post("/api/auth/login", json={"email": dash.email, "password": PASSWORD}).status_code == 429
    # The session itself carries on, and nothing was changed or used up meanwhile.
    status = dash.get("/api/auth/two-factor").json()
    assert status["enabled"] is True and status["recovery_codes_left"] == 10
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e"), {"e": dash.email}
        )
    assert dash.post(off, good).status_code == 200


def test_the_code_step_is_rate_limited(dash, new_client, rt, clock):
    secret, _ = enrol(dash)
    clock.tick()
    challenge = start(new_client(), dash.email)
    statuses = []
    for _ in range(12):
        with rt.engine.begin() as conn:  # keep the account lock out of the way: this is about the limiter
            conn.execute(
                text("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE email = :e"), {"e": dash.email}
            )
        statuses.append(finish(new_client(), challenge, wrong_code(secret)).status_code)
    assert statuses[:10] == [401] * 10 and statuses[10:] == [429, 429]


def test_challenge_expires_and_belongs_to_one_account(dash, new_client, rt, clock):
    secret, _ = enrol(dash)
    other = Dash(new_client())
    other.signup()
    other_secret, _ = enrol(other)
    clock.tick()
    r = finish(new_client(), "0" * 64, code_for(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    # A challenge for one account does not take a code from another.
    challenge = start(new_client(), dash.email)
    assert finish(new_client(), challenge, code_for(other_secret)).status_code == 401
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE login_challenges SET expires_at = now() - interval '1 second' WHERE user_id = :u"),
            {"u": dash.me["user"]["id"]},
        )
    r = finish(new_client(), challenge, code_for(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    # Signing out everywhere else also ends a sign-in that is waiting for its code.
    challenge = start(new_client(), dash.email)
    assert dash.post("/api/auth/sessions/revoke-others").status_code == 200
    assert finish(new_client(), challenge, code_for(secret)).json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    # Old challenges are swept up; one that is still good is left alone.
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE login_challenges SET expires_at = now() - interval '2 days'"))
    assert housekeeping.run(rt)["login_challenges"] >= 1
    challenge = start(new_client(), other.email)
    assert housekeeping.run(rt)["login_challenges"] == 0
    assert finish(new_client(), challenge, code_for(other_secret)).status_code == 200


def test_password_reset_does_not_get_past_the_second_factor(dash, new_client, rt, clock):
    secret, _ = enrol(dash)
    clock.tick()
    waiting = start(new_client(), dash.email)  # someone who knows the old password is half-way in
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE users SET failed_logins = 5 WHERE email = :e"), {"e": dash.email})
    anon = new_client()
    assert anon.post("/api/auth/forgot-password", json={"email": dash.email}).json() == {"ok": True}
    new_password = "a brand new long password"
    r = anon.post(
        "/api/auth/reset-password", json={"token": token_from(last_email(rt, dash.email)), "password": new_password}
    )
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert dash.get("/api/auth/me").status_code == 401
    # The reset ended the half-finished sign-in, and it did not hand out more guesses at the code.
    assert finish(new_client(), waiting, code_for(secret)).json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    assert user_row(rt, dash.email).failed_logins == 5
    c = new_client()
    challenge = start(c, dash.email, new_password)
    assert c.get("/api/auth/me").status_code == 401
    assert finish(c, challenge, code_for(secret)).status_code == 200
    assert user_row(rt, dash.email).failed_logins == 0


def test_password_reset_still_unlocks_an_account_without_two_factor(dash, new_client, rt):
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET failed_logins = 5, locked_until = now() + interval '10 minutes' WHERE email = :e"),
            {"e": dash.email},
        )
    anon = new_client()
    anon.post("/api/auth/forgot-password", json={"email": dash.email})
    token = token_from(last_email(rt, dash.email))
    assert (
        anon.post(
            "/api/auth/reset-password", json={"token": token, "password": "a brand new long password"}
        ).status_code
        == 200
    )
    row = user_row(rt, dash.email)
    assert row.failed_logins == 0 and row.locked_until is None
    assert Dash(new_client()).login(dash.email, "a brand new long password").status_code == 200


# ----------------------------------------------------- switching off, new codes
def test_switching_off_takes_the_password_and_a_code(dash, new_client, rt, clock):
    secret, _ = enrol(dash)
    clock.tick()
    other = new_client()
    assert finish(other, start(other, dash.email), code_for(secret)).status_code == 200
    clock.tick()
    r = dash.post("/api/auth/two-factor/disable", {"current_password": "not my password", "code": code_for(secret)})
    assert r.status_code == 400 and "password" in r.json()["detail"]["message"]
    r = dash.post("/api/auth/two-factor/disable", {"current_password": PASSWORD, "code": wrong_code(secret)})
    assert r.status_code == 400 and "not correct" in r.json()["detail"]["message"]
    assert dash.post("/api/auth/two-factor/disable", {"current_password": PASSWORD}).status_code == 400  # no code
    assert dash.get("/api/auth/two-factor").json()["enabled"] is True
    # The code refused with the wrong password above was not used up by that attempt.
    r = dash.post("/api/auth/two-factor/disable", {"current_password": PASSWORD, "code": code_for(secret)})
    assert r.status_code == 200, r.text
    assert dash.get("/api/auth/two-factor").json() == {"enabled": False, "enabled_at": None, "recovery_codes_left": 0}
    assert dash.get("/api/auth/me").json()["user"]["two_factor_enabled"] is False
    assert other.get("/api/auth/me").status_code == 401
    row = user_row(rt, dash.email)
    assert row.totp_secret_enc is None and row.totp_enabled_at is None and row.totp_last_step is None
    assert row.totp_started_at is None
    with rt.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM recovery_codes WHERE user_id = :u"), {"u": row.id}).scalar() == 0
    assert "two-factor sign-in was turned off" in last_email(rt, dash.email)["subject"]
    assert Dash(new_client()).login(dash.email).status_code == 200  # the password alone again
    r = dash.post("/api/auth/two-factor/disable", {"current_password": PASSWORD, "code": "123456"})
    assert r.status_code == 400 and "not on" in r.json()["detail"]["message"]
    # Without the phone, a recovery code switches it off just as well.
    _, codes = enrol(dash)
    assert (
        dash.post("/api/auth/two-factor/disable", {"current_password": PASSWORD, "code": codes[3]}).status_code == 200
    )


def test_new_recovery_codes_replace_the_old_ones(dash, new_client, rt, clock):
    secret, old = enrol(dash)
    clock.tick()
    path = "/api/auth/two-factor/recovery-codes"
    assert dash.post(path, {"current_password": PASSWORD, "code": wrong_code(secret)}).status_code == 400
    assert dash.post(path, {"current_password": "not my password", "code": code_for(secret)}).status_code == 400
    r = dash.post(path, {"current_password": PASSWORD, "code": code_for(secret)})
    assert r.status_code == 200, r.text
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    assert dash.get("/api/auth/two-factor").json()["recovery_codes_left"] == 10
    assert "new recovery codes were created" in last_email(rt, dash.email)["subject"]
    assert finish(new_client(), start(new_client(), dash.email), old[0]).status_code == 401
    assert finish(new_client(), start(new_client(), dash.email), new[0]).status_code == 200


def test_changes_to_two_factor_are_rate_limited(dash, new_client, rt, clock):
    secret = dash.post("/api/auth/two-factor/setup", {"current_password": PASSWORD}).json()["secret"]
    statuses = []
    for _ in range(11):
        with rt.engine.begin() as conn:  # keep the account lock out of the way: this is about the limiter
            conn.execute(
                text("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE email = :e"), {"e": dash.email}
            )
        statuses.append(dash.post("/api/auth/two-factor/enable", {"code": wrong_code(secret)}).status_code)
    assert statuses[:9] == [400] * 9 and statuses[9:] == [429, 429]
    # Changing the password asks for the password again too, and draws on the same few tries.
    r = dash.post(
        "/api/auth/change-password", {"current_password": PASSWORD, "new_password": "another long password 1"}
    )
    assert r.status_code == 429 and r.headers["retry-after"]
    assert Dash(new_client()).login(dash.email).status_code == 200  # the password was not changed


# ------------------------------------------------------------- keys and operator
def test_secret_is_bound_to_its_account(dash, new_client, rt, clock):
    secret, _ = enrol(dash)
    victim = Dash(new_client())
    victim.signup()
    _, victim_codes = enrol(victim)
    clock.tick()
    with rt.engine.begin() as conn:  # someone with database access plants their own secret on another account
        conn.execute(
            text(
                "UPDATE users SET totp_secret_enc = (SELECT totp_secret_enc FROM users WHERE email = :a) "
                "WHERE email = :v"
            ),
            {"a": dash.email, "v": victim.email},
        )
    try:
        c = new_client()
        challenge = start(c, victim.email)
        r = finish(c, challenge, code_for(secret))
        assert r.status_code == 500 and r.json()["detail"]["code"] == "INTERNAL" and "set-cookie" not in r.headers
        assert c.get("/api/auth/me").status_code == 401
        assert finish(c, challenge, victim_codes[0]).status_code == 200  # the real owner still has a way in
    finally:
        with rt.engine.begin() as conn:  # no key reads that row: do not leave it for the key-rotation tests
            conn.execute(
                text("UPDATE users SET totp_secret_enc = NULL, totp_enabled_at = NULL WHERE email = :v"),
                {"v": victim.email},
            )


def test_key_rotation_re_encrypts_the_secret(monkeypatch, capsys, rt, dash, new_client, clock):
    secret, _ = enrol(dash)
    pending = Dash(new_client())  # set-up started, not confirmed: that secret rotates too
    pending.signup()
    pending.post("/api/auth/two-factor/setup", {"current_password": PASSWORD})
    new_ring = "t2:" + base64.b64encode(b"n" * 32).decode() + "," + TEST_KEYS
    code, out, _ = run_cli(monkeypatch, capsys, "rotate-keys", keys=new_ring)
    assert code == 0 and "two-factor secret(s)" in out
    row, waiting = user_row(rt, dash.email), user_row(rt, pending.email)
    assert row.totp_secret_enc.startswith("v1.t2.") and waiting.totp_secret_enc.startswith("v1.t2.")
    assert Vault.from_spec(new_ring).decrypt_text(row.totp_secret_enc, aad_totp(row.id)) == secret
    # Put the old key back in front so the rest of the suite can still read everything.
    assert run_cli(monkeypatch, capsys, "rotate-keys", keys=TEST_KEYS + "," + new_ring.split(",")[0])[0] == 0
    assert user_row(rt, dash.email).totp_secret_enc.startswith("v1.t1.")
    clock.tick()
    assert finish(new_client(), start(new_client(), dash.email), code_for(secret)).status_code == 200


def test_operator_can_switch_it_off_for_someone_locked_out(monkeypatch, capsys, rt, dash, new_client, clock):
    enrol(dash)
    start(new_client(), dash.email)
    code, out, _ = run_cli(monkeypatch, capsys, "disable-two-factor", "--email", dash.email.upper())
    assert code == 0 and "two-factor sign-in is off" in out
    row = user_row(rt, dash.email)
    assert row.totp_secret_enc is None and row.totp_enabled_at is None
    assert dash.get("/api/auth/me").status_code == 401  # every device is signed out
    with rt.engine.connect() as conn:
        left = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM recovery_codes WHERE user_id = :u) + "
                "(SELECT count(*) FROM login_challenges WHERE user_id = :u)"
            ),
            {"u": row.id},
        ).scalar()
        reset = conn.execute(
            text("SELECT actor_type FROM audit_log WHERE action = 'user.two_factor_reset' AND target_id = :u"),
            {"u": row.id},
        ).scalar()
    assert left == 0 and reset == "system"
    assert "turned off by the operator" in last_email(rt, dash.email)["subject"]
    again = Dash(new_client())
    assert again.login(dash.email).status_code == 200
    # A set-up that was started and never finished is removed the same way.
    assert again.post("/api/auth/two-factor/setup", {"current_password": PASSWORD}).status_code == 200
    assert run_cli(monkeypatch, capsys, "disable-two-factor", "--email", dash.email)[0] == 0
    row = user_row(rt, dash.email)
    assert row.totp_secret_enc is None and row.totp_started_at is None
    code, out, _ = run_cli(monkeypatch, capsys, "disable-two-factor", "--email", dash.email)
    assert code == 0 and "nothing to do" in out
    code, _, err = run_cli(monkeypatch, capsys, "disable-two-factor", "--email", "nobody@example.com")
    assert code == 1 and "no account" in err


def test_migration_0002_applies_and_reverts(settings):
    name = "bdpay_cloud_test_2fa_" + uuid.uuid4().hex[:8]
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    url = settings.database_url.rsplit("/", 1)[0] + "/" + name
    added = {"totp_secret_enc", "totp_enabled_at", "totp_started_at", "totp_last_step"}
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        migrate.upgrade(url, "0001")
        fresh = create_engine(url)
        with fresh.begin() as conn:  # an account from before two-factor sign-in existed
            conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES ('usr_old', 'old@example.com', 'x')")
            )
        assert not added & {c["name"] for c in inspect(fresh).get_columns("users")}
        migrate.upgrade(url)
        assert added <= {c["name"] for c in inspect(fresh).get_columns("users")}
        assert {"login_challenges", "recovery_codes"} <= set(inspect(fresh).get_table_names())
        with fresh.begin() as conn:
            row = conn.execute(text("SELECT totp_secret_enc, totp_enabled_at FROM users WHERE id = 'usr_old'")).one()
            assert tuple(row) == (None, None)  # nobody is switched on by the upgrade
            conn.execute(text("INSERT INTO recovery_codes (id, user_id) VALUES ('h', 'usr_old')"))
        migrate.downgrade(url, "0001")
        assert not added & {c["name"] for c in inspect(fresh).get_columns("users")}
        assert not {"login_challenges", "recovery_codes"} & set(inspect(fresh).get_table_names())
        with fresh.connect() as conn:
            assert conn.execute(text("SELECT email FROM users")).scalars().all() == ["old@example.com"]
        migrate.upgrade(url)
        assert migrate.is_current(fresh)
        fresh.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


# ------------------------------------------------------ required by a workspace
def test_workspace_can_require_two_factor_for_live_changes(dash, new_client, rt, clock):
    dash.go_live()
    base = f"/api/workspaces/{dash.slug}"
    dash.post(f"{base}/invitations", {"email": "admin.two@example.com", "role": "admin"})
    admin = Dash(new_client())
    r = admin.signup(email="admin.two@example.com", invite_token=token_from(last_email(rt, "admin.two@example.com")))
    assert r.status_code == 200, r.text
    assert dash.get(base).json()["require_two_factor"] is False
    live_change = lambda who: who.patch(f"{who.env('live')}/settings", {"result_url": "https://shop.example/thanks"})
    assert live_change(admin).status_code == 200

    # Owners decide, and only once their own account has a second factor.
    assert admin.patch(base, {"require_two_factor": True}).status_code == 403
    r = dash.patch(base, {"require_two_factor": True})
    assert r.status_code == 400 and "your own account first" in r.json()["detail"]["message"]
    enrol(dash)
    r = dash.patch(base, {"require_two_factor": True})
    assert r.status_code == 200 and r.json()["require_two_factor"] is True

    r = live_change(admin)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "TWO_FACTOR_REQUIRED"
    assert admin.post(f"{admin.env('live')}/api-keys", {"name": "blocked"}).status_code == 403
    assert admin.get(f"{admin.env('live')}/gateways").status_code == 200  # looking is still fine
    assert (
        admin.patch(f"{admin.env()}/settings", {"result_url": "https://shop.example/thanks"}).status_code == 200
    )  # sandbox
    assert live_change(dash).status_code == 200
    members = {m["email"]: m["two_factor"] for m in dash.get(f"{base}/members").json()["members"]}
    assert members == {dash.email: True, "admin.two@example.com": False}

    enrol(admin)
    assert live_change(admin).status_code == 200
    # An analyst sees only their own.
    dash.post(f"{base}/invitations", {"email": "reader@example.com", "role": "analyst"})
    reader = Dash(new_client())
    reader.signup(email="reader@example.com", invite_token=token_from(last_email(rt, "reader@example.com")))
    seen = {m["email"]: m["two_factor"] for m in reader.get(f"{base}/members").json()["members"]}
    assert seen == {dash.email: None, "admin.two@example.com": None, "reader@example.com": False}

    assert dash.patch(base, {"require_two_factor": False}).json()["require_two_factor"] is False
    assert dash.patch(base, {"display_name": "Rahim Store"}).json()["require_two_factor"] is False
    with rt.engine.connect() as conn:
        fields = (
            conn.execute(
                text(
                    "SELECT detail->'fields' FROM audit_log WHERE workspace_id = :w AND action = 'workspace.updated' "
                    "ORDER BY id"
                ),
                {"w": dash.workspace_id},
            )
            .scalars()
            .all()
        )
    assert fields[:2] == [["require_two_factor"], ["require_two_factor"]]


def test_an_owner_without_the_factor_cannot_take_the_workspace_rule_away(dash, new_client, rt, clock):
    dash.go_live()
    base = f"/api/workspaces/{dash.slug}"
    enrol(dash)
    assert dash.patch(base, {"require_two_factor": True}).status_code == 200
    # A second owner who never set one up. Their password alone must not be enough to drop the rule.
    dash.post(f"{base}/invitations", {"email": "owner.two@example.com", "role": "owner"})
    second = Dash(new_client())
    r = second.signup(email="owner.two@example.com", invite_token=token_from(last_email(rt, "owner.two@example.com")))
    assert r.status_code == 200 and second.me["user"]["two_factor_enabled"] is False

    def live_change():
        return second.patch(f"{second.env('live')}/settings", {"result_url": "https://shop.example/thanks"})

    assert live_change().json()["detail"]["code"] == "TWO_FACTOR_REQUIRED"
    for wanted in (False, True):
        r = second.patch(base, {"require_two_factor": wanted})
        assert r.status_code == 400 and "your own account first" in r.json()["detail"]["message"], wanted
    assert dash.get(base).json()["require_two_factor"] is True
    assert live_change().status_code == 403
    assert second.patch(base, {"display_name": "Rahim Store"}).status_code == 200  # other settings are not held back
    # Once their own account has a second factor, the decision is theirs as well.
    enrol(second)
    assert second.patch(base, {"require_two_factor": False}).json()["require_two_factor"] is False
    assert live_change().status_code == 200
