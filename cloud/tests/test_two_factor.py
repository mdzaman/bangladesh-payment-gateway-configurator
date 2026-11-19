"""Two-factor sign-in: TOTP codes from an authenticator app, and recovery codes."""

import base64
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from argon2 import PasswordHasher
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from bdpay_cloud import migrate
from bdpay_cloud.crypto import Vault, VaultError, aad_totp
from bdpay_cloud.db import session_scope
from bdpay_cloud.security import passwords, totp
from bdpay_cloud.services import auth
from bdpay_cloud.workers import housekeeping

from conftest import TEST_KEYS
from helpers import PASSWORD, Dash, last_email, run_cli, token_from

SETUP = "/api/auth/two-factor/setup"
CONFIRM = "/api/auth/two-factor/confirm"
DISABLE = "/api/auth/two-factor/disable"
NEW_CODES = "/api/auth/two-factor/recovery-codes"
STATUS = "/api/auth/two-factor"


def user_row(rt, email: str):
    with rt.engine.connect() as conn:
        return conn.execute(text("SELECT * FROM users WHERE email = :e"), {"e": email}).one()


def audit_actions(rt, email: str) -> list[str]:
    with rt.engine.connect() as conn:
        rows = conn.execute(text("SELECT action FROM audit_log WHERE actor_label = :e ORDER BY id"), {"e": email})
        return list(rows.scalars())


def message(r) -> str:
    return r.json()["detail"]["message"]


def wrong_code(clock, secret: str) -> str:
    """Six digits that are not this secret's code now, nor one step either side."""
    near = {totp.code_at(secret, clock.t + off) for off in (-30, 0, 30)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in near)


# ------------------------------------------------------------- the algorithm
def test_codes_match_the_rfc_test_vectors():
    key = b"12345678901234567890"
    # RFC 4226, appendix D: HOTP for counters 0 to 9.
    assert [totp.hotp(key, n) for n in range(10)] == [
        "755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489",
    ]  # fmt: skip
    # RFC 6238, appendix B, the SHA-1 rows (eight digits there; six is the same number, shortened).
    secret = base64.b32encode(key).decode()
    for at, eight in (
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ):
        assert totp.hotp(key, at // 30, digits=8) == eight
        assert totp.code_at(secret, at) == eight[2:]


def test_one_step_either_side_is_accepted_and_no_more():
    secret, at = totp.new_secret(), 1_800_000_015.0  # the middle of a step
    step = int(at // 30)
    assert len(base64.b32decode(secret)) == 20 and totp.new_secret() != secret
    assert totp.match(secret, totp.code_at(secret, at), at) == step
    assert totp.match(secret, totp.code_at(secret, at - 30), at) == step - 1  # the phone is a little slow
    assert totp.match(secret, totp.code_at(secret, at + 30), at) == step + 1  # or a little fast
    for off in (-90, -60, 60, 90):
        code = totp.code_at(secret, at + off)
        if code not in {totp.code_at(secret, at + near) for near in (-30, 0, 30)}:  # one chance in 300,000 of a twin
            assert totp.match(secret, code, at) is None
    assert totp.match(totp.new_secret(), totp.code_at(secret, at), at) is None  # someone else's secret
    assert totp.match(secret, totp.code_at(secret, 0), 0) == 0  # no step before the first one


def test_what_people_type_is_read_generously_but_not_loosely():
    assert totp.clean_code(" 123 456 ") == "123456" and totp.clean_code("000000") == "000000"
    assert totp.clean_code("১২৩৪৫৬") == "123456"  # a Bangla keyboard
    for bad in ("", "12345", "1234567", "12345a", "123-456", "abcdef"):
        assert totp.clean_code(bad) is None, bad
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", account="rahim+shop@example.com", issuer="Pay: Dhaka")
    assert uri == (
        "otpauth://totp/Pay%20Dhaka:rahim%2Bshop%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Pay%20Dhaka"
        "&algorithm=SHA1&digits=6&period=30"
    )


# ----------------------------------------------------------------- enrolment
def test_enrol_and_confirm(dash, new_client, rt, clock):
    assert dash.me["user"]["two_factor_enabled"] is False
    assert dash.get(STATUS).json() == {
        "enabled": False,
        "enabled_at": None,
        "setup_pending": False,
        "recovery_codes_left": 0,
    }
    # Starting needs the password again, not just the session.
    r = dash.post(SETUP, {"current_password": "not my password"})
    assert r.status_code == 400 and message(r) == "Your current password is not correct"
    assert user_row(rt, dash.email).totp_secret_enc is None

    setup = dash.post(SETUP, {"current_password": PASSWORD}).json()
    secret = setup["secret"]
    assert len(base64.b32decode(secret)) == 20 and (setup["digits"], setup["period"]) == (6, 30)
    assert (
        setup["otpauth_uri"].startswith("otpauth://totp/bdpay%20cloud:") and f"secret={secret}&" in setup["otpauth_uri"]
    )
    assert setup["account"] == dash.email and setup["issuer"] == "bdpay cloud"
    # Not active yet: signing in is still one step, and the account says so.
    assert dash.get(STATUS).json()["setup_pending"] is True and dash.get(STATUS).json()["enabled"] is False
    assert dash.get("/api/auth/me").json()["user"]["two_factor_enabled"] is False
    assert "csrf_token" in Dash(new_client()).login(dash.email).json()

    r = dash.post(CONFIRM, {"code": "12345"})
    assert r.status_code == 400 and "6-digit code" in message(r)
    r = dash.post(CONFIRM, {"code": wrong_code(clock, secret)})
    assert r.status_code == 400 and "not correct" in message(r)
    assert user_row(rt, dash.email).totp_enabled_at is None

    done = dash.post(CONFIRM, {"code": clock.code(secret)})
    assert done.status_code == 200 and done.json()["enabled"] is True
    codes = done.json()["recovery_codes"]
    assert len(codes) == len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    status = dash.get(STATUS).json()
    assert status["enabled"] is True and status["enabled_at"] and status["recovery_codes_left"] == 10
    assert status["setup_pending"] is False and dash.get("/api/auth/me").json()["user"]["two_factor_enabled"] is True
    assert audit_actions(rt, dash.email)[-1] == "user.two_factor_enabled"
    # Once on, it cannot be set up again over the top.
    assert dash.post(SETUP, {"current_password": PASSWORD}).status_code == 409
    assert dash.post(CONFIRM, {"code": clock.tick().code(secret)}).status_code == 409


def test_the_secret_and_the_recovery_codes_are_never_stored_or_shown_in_the_clear(dash, rt, clock):
    secret, codes = dash.enable_two_factor(clock)
    row = user_row(rt, dash.email)
    assert row.totp_secret_enc.startswith("v1.t1.") and secret not in row.totp_secret_enc
    assert rt.vault.decrypt_text(row.totp_secret_enc, aad_totp(row.id)) == secret
    with pytest.raises(VaultError):  # bound to this account: useless in anyone else's row
        rt.vault.decrypt_text(row.totp_secret_enc, aad_totp("usr_someone_else"))
    with rt.engine.connect() as conn:
        hashes = conn.execute(text("SELECT code_hash FROM recovery_codes WHERE user_id = :u"), {"u": row.id}).scalars()
        hashes = list(hashes)
    assert len(hashes) == 10 and all(h.startswith("$argon2id$") for h in hashes)
    stored = " ".join(hashes)
    assert not any(c in stored or c.replace("-", "") in stored for c in codes)
    # Nothing shows either again.
    for path in (STATUS, "/api/auth/me", "/api/auth/sessions"):
        body = dash.get(path).text
        assert secret not in body and not any(c in body for c in codes), path


def test_a_started_set_up_can_be_restarted_and_runs_out(dash, new_client, rt, clock):
    first = dash.post(SETUP, {"current_password": PASSWORD}).json()["secret"]
    second = dash.post(SETUP, {"current_password": PASSWORD}).json()["secret"]
    assert first != second
    if clock.code(first) != clock.code(second):
        assert dash.post(CONFIRM, {"code": clock.code(first)}).status_code == 400  # the replaced secret is dead
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET totp_started_at = now() - interval '16 minutes' WHERE email = :e"), {"e": dash.email}
        )
    assert dash.get(STATUS).json()["setup_pending"] is False
    r = dash.post(CONFIRM, {"code": clock.code(second)})
    assert r.status_code == 400 and "expired" in message(r)
    fresh = Dash(new_client())  # the same answer where nothing was ever started
    fresh.signup()
    assert "never started" in message(fresh.post(CONFIRM, {"code": "123456"}))
    third = dash.post(SETUP, {"current_password": PASSWORD}).json()["secret"]
    assert dash.post(CONFIRM, {"code": clock.code(third)}).status_code == 200


def test_two_factor_endpoints_need_a_session_and_the_csrf_token(dash, client):
    for path in (SETUP, CONFIRM, DISABLE, NEW_CODES):
        assert client.post(path, json={}).status_code == 401, path
        assert dash.c.post(path, json={"current_password": PASSWORD, "code": "123456"}).status_code == 403, path
    assert client.get(STATUS).status_code == 401


# ------------------------------------------------------------------ sign-in
def test_sign_in_takes_the_password_and_then_a_code(dash, new_client, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    last_sign_in = user_row(rt, dash.email).last_login_at
    visitor = Dash(new_client())
    r = visitor.login(dash.email)
    assert r.status_code == 200 and set(r.json()) == {"two_factor_required", "challenge", "expires_in"}
    assert r.json()["two_factor_required"] is True and r.json()["expires_in"] == 300
    # The password alone opened nothing: no cookie, no session, no account data.
    assert "set-cookie" not in r.headers and visitor.c.cookies.get("__Host-bdpay_session") is None
    assert visitor.get("/api/auth/me").status_code == 401
    assert user_row(rt, dash.email).last_login_at == last_sign_in

    r = visitor.second_step(code=clock.tick().code(secret))
    assert r.status_code == 200 and r.json()["user"]["email"] == dash.email and r.json()["csrf_token"]
    assert r.headers["set-cookie"].startswith("__Host-bdpay_session=")
    assert visitor.get("/api/auth/me").json()["user"]["two_factor_enabled"] is True
    assert visitor.post("/api/workspaces", {"name": "Opened after two steps"}).status_code == 200
    assert user_row(rt, dash.email).last_login_at > last_sign_in
    # Wrong password and unknown account still get one and the same answer.
    assert message(Dash(new_client()).login(dash.email, "wrong password entirely")) == "Invalid email or password"
    assert message(Dash(new_client()).login("nobody@example.com")) == "Invalid email or password"


def test_a_wrong_code_is_refused(dash, new_client, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    visitor = Dash(new_client())
    visitor.login(dash.email)
    wrong = wrong_code(clock.tick(), secret)
    r = visitor.second_step(code=wrong)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "UNAUTHENTICATED" and "not correct" in message(r)
    assert "set-cookie" not in r.headers and visitor.get("/api/auth/me").status_code == 401
    assert user_row(rt, dash.email).failed_logins == 1
    # Something that cannot be a code is a typing mistake, not a guess.
    for body in ({"code": "12345"}, {"recovery_code": "nope"}, {}, {"code": "123456", "recovery_code": "abcde-12345"}):
        assert visitor.second_step(**body).status_code == 400, body
    assert user_row(rt, dash.email).failed_logins == 1
    # The same sign-in can still be finished with the right code, and that clears the count.
    assert visitor.second_step(code=clock.code(secret)).status_code == 200
    assert user_row(rt, dash.email).failed_logins == 0


def test_a_code_cannot_be_replayed(dash, new_client, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    # The code that confirmed the set-up is spent: it does not also open a sign-in.
    first = Dash(new_client())
    first.login(dash.email)
    r = first.second_step(code=clock.code(secret))
    assert r.status_code == 401 and "already been used" in message(r)

    code = clock.tick().code(secret)
    assert first.second_step(code=code).status_code == 200
    # Someone who watched that code being typed tries it within the same 30 seconds.
    thief = Dash(new_client())
    thief.login(dash.email)
    r = thief.second_step(code=code)
    assert r.status_code == 401 and "already been used" in message(r) and thief.get("/api/auth/me").status_code == 401
    assert user_row(rt, dash.email).failed_logins == 1  # it counts as a failed attempt
    # Half a minute later it is still inside the clock allowance, and still refused.
    clock.tick()
    assert thief.second_step(code=code).status_code == 401
    # A phone that runs fast shows the next code early. Accepting it retires the current
    # one, which nobody has used: codes are only ever accepted in order.
    assert thief.second_step(code=totp.code_at(secret, clock.t + 30)).status_code == 200
    late = Dash(new_client())
    late.login(dash.email)
    assert "already been used" in message(late.second_step(code=clock.code(secret)))
    assert late.second_step(code=clock.tick(2).code(secret)).status_code == 200


def test_a_recovery_code_works_once(dash, new_client, rt, clock):
    _, codes = dash.enable_two_factor(clock)
    visitor = Dash(new_client())
    visitor.login(dash.email)
    r = visitor.second_step(recovery_code=codes[3])
    assert r.status_code == 200 and visitor.get("/api/auth/me").status_code == 200
    assert visitor.get(STATUS).json()["recovery_codes_left"] == 9
    with rt.engine.connect() as conn:
        used = conn.execute(
            text("SELECT detail FROM audit_log WHERE action = 'user.recovery_code_used' AND actor_label = :e"),
            {"e": dash.email},
        ).scalar_one()
    assert used == {"remaining": 9}
    # The same code again, however it is typed.
    again = Dash(new_client())
    again.login(dash.email)
    for attempt in (codes[3], codes[3].upper(), codes[3].replace("-", " ")):
        r = again.second_step(recovery_code=attempt)
        assert r.status_code == 401 and "used already" in message(r), attempt
    assert again.get("/api/auth/me").status_code == 401 and user_row(rt, dash.email).failed_logins == 3
    # A different one works, typed in capitals without the dash.
    assert again.second_step(recovery_code=codes[7].replace("-", "").upper()).status_code == 200
    assert again.get(STATUS).json()["recovery_codes_left"] == 8


def test_recovery_codes_forgive_lookalike_characters():
    from bdpay_cloud.services.auth import _clean_recovery_code as clean

    assert clean("ABCDE-12345") == clean("abcde 12345") == clean(" abcde12345 ") == "abcde12345"
    assert clean("O0oIl-1ilLo") == "00011-11110".replace("-", "")  # o is zero, i and l are one
    for bad in ("", "abcde-1234", "abcde-123456", "abcde-1234u", "abcde_12345", "১২৩৪৫-১২৩৪৫"):
        assert clean(bad) is None, bad


def test_wrong_codes_lock_the_account_and_the_password_does_not_clear_the_count(dash, new_client, app, rt, clock):
    secret, codes = dash.enable_two_factor(clock)
    wrong = wrong_code(clock.tick(), secret)
    for attempt in range(8):
        app.state.limiter.reset()  # isolate the account lock from the rate limit
        visitor = Dash(new_client())
        # Typing the right password again each time must not hand out seven more guesses.
        assert "challenge" in visitor.login(dash.email).json(), attempt
        assert user_row(rt, dash.email).failed_logins == attempt
        assert visitor.second_step(code=wrong).status_code == 401, attempt
    row = user_row(rt, dash.email)
    assert row.locked_until is not None and row.failed_logins == 0
    app.state.limiter.reset()
    # Locked: the right code, a recovery code and the password are all turned away.
    for factor in ({"code": clock.code(secret)}, {"recovery_code": codes[0]}):
        r = visitor.second_step(**factor)
        assert r.status_code == 429 and "Too many failed attempts" in message(r), factor
    assert Dash(new_client()).login(dash.email).status_code == 429
    # The signed-in session cannot switch it off or mint recovery codes meanwhile either.
    body = {"current_password": PASSWORD, "code": clock.code(secret)}
    assert dash.post(DISABLE, body).status_code == 429 and dash.post(NEW_CODES, body).status_code == 429
    assert dash.get(STATUS).json()["recovery_codes_left"] == 10  # nothing was spent while locked
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e"), {"e": dash.email}
        )
    visitor = Dash(new_client())
    visitor.login(dash.email)
    assert visitor.second_step(code=clock.code(secret)).status_code == 200


def test_wrong_passwords_and_wrong_codes_share_one_count(dash, new_client, app, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    wrong = wrong_code(clock.tick(), secret)
    for attempt in range(4):
        app.state.limiter.reset()
        assert Dash(new_client()).login(dash.email, "definitely wrong").status_code == 401
        visitor = Dash(new_client())
        visitor.login(dash.email)
        assert visitor.second_step(code=wrong).status_code == 401, attempt
    assert user_row(rt, dash.email).locked_until is not None


@pytest.mark.parametrize("step", ["password", "code"])
def test_parallel_guesses_are_each_counted(dash, new_client, app, rt, clock, step):
    secret, _ = dash.enable_two_factor(clock)
    wrong = wrong_code(clock.tick(), secret)
    visitors = []
    for _ in range(8):
        app.state.limiter.reset()
        visitors.append(Dash(new_client()))
        if step == "code":
            visitors[-1].login(dash.email)
    app.state.limiter.reset()
    barrier = threading.Barrier(8)

    def guess(visitor):
        barrier.wait()
        if step == "code":
            return visitor.second_step(code=wrong).status_code
        return visitor.login(dash.email, "definitely wrong").status_code

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(guess, visitors))
    # Eight wrong guesses at once are eight failures, not one: the account is locked.
    assert statuses == [401] * 8
    row = user_row(rt, dash.email)
    assert row.locked_until is not None and row.failed_logins == 0


def test_one_code_opens_one_session_however_many_ask_at_once(dash, new_client, app, rt, clock):
    secret, codes = dash.enable_two_factor(clock)
    for factor in ({"code": clock.tick().code(secret)}, {"recovery_code": codes[0]}):
        visitors = []
        for _ in range(4):
            visitors.append(Dash(new_client()))
            visitors[-1].login(dash.email)
        app.state.limiter.reset()
        barrier = threading.Barrier(4)

        def use(visitor, factor=factor, barrier=barrier):
            barrier.wait()
            return visitor.second_step(**factor).status_code

        with ThreadPoolExecutor(4) as pool:
            statuses = sorted(pool.map(use, visitors))
        assert statuses == [200, 401, 401, 401], factor
        with rt.engine.begin() as conn:  # three refusals are three failed attempts: start the next round clean
            conn.execute(text("UPDATE users SET failed_logins = 0 WHERE email = :e"), {"e": dash.email})
    assert dash.get(STATUS).json()["recovery_codes_left"] == 9


def test_the_second_step_shares_the_sign_in_rate_limit(dash, new_client, clock):
    dash.enable_two_factor(clock)
    visitor = Dash(new_client())
    assert visitor.login(dash.email).status_code == 200  # 1 of the 10 attempts per account per 15 minutes
    statuses = [visitor.second_step(code="12345").status_code for _ in range(11)]  # mistyped, so no lock-out
    assert statuses[:9] == [400] * 9 and statuses[9:] == [429, 429]
    assert "Too many requests" in message(visitor.second_step(code="12345"))
    assert Dash(new_client()).login(dash.email).status_code == 429  # one budget for both steps


# ------------------------------------------- the same rules while signed in
CHANGE = "/api/auth/change-password"


def test_a_wrong_password_on_the_change_password_page_counts_towards_the_lock(dash, new_client, app, rt):
    """No two-factor needed for this one: an open session is not a place to guess the password."""
    new = "another long password 1"
    for attempt in range(8):
        app.state.limiter.reset()
        assert user_row(rt, dash.email).failed_logins == attempt
        r = dash.post(CHANGE, {"current_password": "definitely wrong", "new_password": new})
        assert r.status_code == 400 and message(r) == "Your current password is not correct", attempt
    row = user_row(rt, dash.email)
    assert row.locked_until is not None and row.failed_logins == 0
    app.state.limiter.reset()
    # Locked: the right password is turned away here as it is at sign-in, and nothing changed.
    r = dash.post(CHANGE, {"current_password": PASSWORD, "new_password": new})
    assert r.status_code == 429 and "Too many failed attempts" in message(r)
    assert Dash(new_client()).login(dash.email).status_code == 429
    with rt.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET locked_until = now() - interval '1 second' WHERE email = :e"), {"e": dash.email}
        )
    assert Dash(new_client()).login(dash.email, new).status_code == 401
    assert dash.post(CHANGE, {"current_password": PASSWORD, "new_password": new}).status_code == 200
    assert Dash(new_client()).login(dash.email, new).status_code == 200


def test_pages_that_ask_for_the_password_again_share_a_rate_limit(dash):
    # A new password that is too short is refused before anything counts as a failure,
    # so this reaches the rate limit and not the lock-out: 20 tries per 15 minutes.
    body = {"current_password": PASSWORD, "new_password": "short"}
    statuses = [dash.post(CHANGE, body).status_code for _ in range(22)]
    assert statuses[:20] == [400] * 20 and statuses[20:] == [429, 429]
    assert "Too many requests" in message(dash.post(CHANGE, body))
    assert dash.post(SETUP, {"current_password": PASSWORD}).status_code == 429  # the two-factor pages draw on it too


@pytest.mark.parametrize("meanwhile", ["locked", "password changed", "password re-hashed"])
def test_a_right_password_is_looked_at_again_once_the_account_is_locked(dash, new_client, rt, monkeypatch, meanwhile):
    """Checking a password is slow, and the account row is not held for it. Whatever
    happened to the account during the check is seen before a session is made."""
    other_hasher = PasswordHasher(time_cost=2, memory_cost=8, parallelism=1)
    change = {
        "locked": ("UPDATE users SET locked_until = now() + interval '15 minutes' WHERE email = :e", {}),
        "password changed": (
            "UPDATE users SET password_hash = :h WHERE email = :e",
            {"h": other_hasher.hash("x" * 20)},
        ),
        "password re-hashed": (
            "UPDATE users SET password_hash = :h WHERE email = :e",
            {"h": other_hasher.hash(PASSWORD)},
        ),
    }[meanwhile]
    real, happened = passwords.verify_password, []

    def check_and_then(password_hash, password):
        ok = real(password_hash, password)
        if ok and not happened:  # the other request lands right after this one's check passed
            happened.append(meanwhile)
            with rt.engine.begin() as conn:
                conn.execute(text(change[0]), {"e": dash.email, **change[1]})
        return ok

    monkeypatch.setattr(passwords, "verify_password", check_and_then)
    visitor = Dash(new_client())
    r = visitor.login(dash.email)
    assert happened == [meanwhile]
    if meanwhile == "password re-hashed":  # still the right password: only the stored form of it changed
        assert r.status_code == 200 and visitor.get("/api/auth/me").status_code == 200
        return
    assert r.status_code == (429 if meanwhile == "locked" else 401)
    assert "set-cookie" not in r.headers and visitor.get("/api/auth/me").status_code == 401
    with rt.engine.connect() as conn:  # and no session row was left behind for it
        sessions = conn.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = :u"), {"u": dash.me["user"]["id"]}
        ).scalar()
    assert sessions == 1  # the one the fixture signed up with


def test_a_reset_and_another_forgot_password_at_the_same_moment_do_not_deadlock(dash, rt):
    """The account row is locked FOR NO KEY UPDATE. With plain FOR UPDATE these two
    wait for each other: the reset holds the account and wants its link's row, the
    new request holds that row and wants to insert a link that refers to the account."""
    with session_scope(rt.sessions) as db:
        auth.request_password_reset(db, rt.settings, dash.email)
    token = token_from(last_email(rt, dash.email))
    reset_holds_account, forgot_holds_link = threading.Event(), threading.Event()
    outcome: dict[str, str] = {}

    def use_the_link():
        try:
            with session_scope(rt.sessions) as db:
                auth.reset_password(db, token, "a brand new long password")  # what the reset page calls
                reset_holds_account.set()
                forgot_holds_link.wait(5)
                time.sleep(0.4)  # long enough for the other request to reach its commit
            outcome["reset"] = "committed"
        except DBAPIError as e:
            outcome["reset"] = type(e.orig).__name__

    def ask_for_another():
        try:
            reset_holds_account.wait(5)
            with session_scope(rt.sessions) as db:
                auth.request_password_reset(db, rt.settings, dash.email)  # what the forgot-password page calls
                forgot_holds_link.set()
            outcome["forgot"] = "committed"
        except DBAPIError as e:
            outcome["forgot"] = type(e.orig).__name__

    threads = [threading.Thread(target=use_the_link), threading.Thread(target=ask_for_another)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert outcome == {"reset": "committed", "forgot": "committed"}


# ------------------------------------------------------------ the challenge
def test_a_challenge_is_short_lived_single_use_and_good_for_nothing_else(dash, new_client, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    visitor = Dash(new_client())
    challenge = visitor.login(dash.email).json()["challenge"]
    with rt.engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM login_challenges WHERE user_id = :u"), {"u": dash.me["user"]["id"]}
        ).one()
    assert challenge not in (row.id, row.password_tag) and len(row.id) == 64  # only its hash is stored
    # It is not a session cookie, a CSRF token, or an API key.
    visitor.c.cookies.set("__Host-bdpay_session", challenge)
    assert visitor.c.get("/api/auth/me").status_code == 401
    assert visitor.c.get("/v1/gateways", headers={"Authorization": f"Bearer {challenge}"}).status_code == 401
    visitor.c.cookies.clear()
    # Used once, it is finished, even with a code that would otherwise be good.
    assert visitor.second_step(code=clock.tick().code(secret)).status_code == 200
    r = visitor.second_step(code=clock.tick().code(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    # Left for more than five minutes, it has expired.
    late = Dash(new_client())
    late.login(dash.email)
    with rt.engine.begin() as conn:
        conn.execute(text("UPDATE login_challenges SET expires_at = now() - interval '1 second' WHERE used_at IS NULL"))
    r = late.second_step(code=clock.tick().code(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "CHALLENGE_EXPIRED" and "password again" in message(r)
    # A made-up one is the same story, and none of these count against the account.
    late.challenge = "0" * 64
    assert late.second_step(code=clock.code(secret)).json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    assert user_row(rt, dash.email).failed_logins == 0


def test_a_password_change_ends_sign_ins_that_were_half_way(dash, new_client, clock):
    secret, _ = dash.enable_two_factor(clock)
    visitor = Dash(new_client())
    visitor.login(dash.email)  # passed the password step with the old password
    r = dash.post(
        "/api/auth/change-password", {"current_password": PASSWORD, "new_password": "another long password 1"}
    )
    assert r.status_code == 200
    r = visitor.second_step(code=clock.tick().code(secret))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    assert visitor.login(dash.email, "another long password 1").status_code == 200
    assert visitor.second_step(code=clock.code(secret)).status_code == 200


def test_housekeeping_removes_old_challenges_and_abandoned_set_ups(dash, new_client, rt, clock):
    dash.enable_two_factor(clock)
    for _ in range(2):
        Dash(new_client()).login(dash.email)
    abandoned, recent = Dash(new_client()), Dash(new_client())
    for person in (abandoned, recent):
        person.signup()
        person.post(SETUP, {"current_password": PASSWORD})
    uid = dash.me["user"]["id"]
    with rt.engine.begin() as conn:
        one = conn.execute(text("SELECT id FROM login_challenges WHERE user_id = :u LIMIT 1"), {"u": uid}).scalar()
        conn.execute(
            text("UPDATE login_challenges SET expires_at = now() - interval '2 days' WHERE id = :i"), {"i": one}
        )
        conn.execute(
            text("UPDATE users SET totp_started_at = now() - interval '2 days' WHERE email = :e"),
            {"e": abandoned.email},
        )
    done = housekeeping.run(rt)
    assert done["login_challenges"] >= 1 and done["two_factor_setups"] >= 1
    with rt.engine.connect() as conn:
        left = conn.execute(text("SELECT count(*) FROM login_challenges WHERE user_id = :u"), {"u": uid}).scalar()
    assert left == 1
    # The secret of the set-up nobody finished is gone; a fresh one and a confirmed one are untouched.
    assert user_row(rt, abandoned.email).totp_secret_enc is None
    assert user_row(rt, recent.email).totp_secret_enc is not None
    assert user_row(rt, dash.email).totp_secret_enc is not None and dash.get(STATUS).json()["enabled"] is True


# --------------------------------------------------- switching off, new codes
def test_disable_needs_the_password_and_the_second_factor(dash, new_client, rt, clock):
    secret, codes = dash.enable_two_factor(clock)
    code, wrong = clock.tick().code(secret), wrong_code(clock, secret)
    for body, why in (
        ({"current_password": "not my password", "code": code}, "password is not correct"),
        ({"current_password": PASSWORD}, "or one recovery code"),
        ({"current_password": PASSWORD, "code": wrong}, "not correct"),
        ({"current_password": PASSWORD, "recovery_code": "abcde-12345"}, "not correct"),
    ):
        r = dash.post(DISABLE, body)
        # 400, never 401: the dashboard would take 401 to mean the session had ended.
        assert r.status_code == 400 and why in message(r), body
    assert user_row(rt, dash.email).totp_enabled_at is not None and user_row(rt, dash.email).failed_logins == 3
    # A wrong password did not let the code through: it is still unspent and works now.
    r = dash.post(DISABLE, {"current_password": PASSWORD, "code": code})
    assert r.status_code == 200 and r.json() == {"enabled": False}
    row = user_row(rt, dash.email)
    assert (row.totp_secret_enc, row.totp_enabled_at, row.totp_started_at, row.totp_last_step) == (None,) * 4
    with rt.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM recovery_codes WHERE user_id = :u"), {"u": row.id}).scalar() == 0
    assert dash.get("/api/auth/me").json()["user"]["two_factor_enabled"] is False
    assert audit_actions(rt, dash.email)[-1] == "user.two_factor_disabled"
    # Signing in is one step again, and the old recovery codes open nothing.
    visitor = Dash(new_client())
    assert "csrf_token" in visitor.login(dash.email).json()
    assert dash.post(DISABLE, {"current_password": PASSWORD, "recovery_code": codes[0]}).status_code == 400


def test_a_recovery_code_can_switch_it_off_when_the_phone_is_gone(dash, new_client, rt, clock):
    _, codes = dash.enable_two_factor(clock)
    visitor = Dash(new_client())
    visitor.login(dash.email)
    assert visitor.second_step(recovery_code=codes[0]).status_code == 200
    assert visitor.post(DISABLE, {"current_password": PASSWORD, "recovery_code": codes[0]}).status_code == 400  # spent
    assert visitor.post(DISABLE, {"current_password": PASSWORD, "recovery_code": codes[1]}).status_code == 200
    assert user_row(rt, dash.email).totp_secret_enc is None
    # ...and it can be set up afresh, with a new secret and new codes.
    _, fresh = visitor.enable_two_factor(clock.tick())
    assert set(fresh).isdisjoint(codes) and len(fresh) == 10


def test_switching_it_on_or_off_ends_other_sessions(dash, new_client, clock):
    elsewhere = Dash(new_client())
    elsewhere.login(dash.email)
    assert elsewhere.get("/api/auth/me").status_code == 200
    secret, _ = dash.enable_two_factor(clock)
    assert elsewhere.get("/api/auth/me").status_code == 401 and dash.get("/api/auth/me").status_code == 200
    elsewhere.login(dash.email)
    assert elsewhere.second_step(code=clock.tick().code(secret)).status_code == 200
    assert dash.post(DISABLE, {"current_password": PASSWORD, "code": clock.tick().code(secret)}).status_code == 200
    assert elsewhere.get("/api/auth/me").status_code == 401 and dash.get("/api/auth/me").status_code == 200


def test_new_recovery_codes_replace_the_old_ones(dash, new_client, rt, clock):
    secret, old = dash.enable_two_factor(clock)
    assert (
        dash.post(NEW_CODES, {"current_password": "not my password", "code": clock.tick().code(secret)}).status_code
        == 400
    )
    assert dash.post(NEW_CODES, {"current_password": PASSWORD}).status_code == 400
    r = dash.post(NEW_CODES, {"current_password": PASSWORD, "code": clock.code(secret)})
    assert r.status_code == 200
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and set(new).isdisjoint(old)
    assert audit_actions(rt, dash.email)[-1] == "user.recovery_codes_regenerated"
    assert dash.get(STATUS).json()["recovery_codes_left"] == 10
    visitor = Dash(new_client())
    visitor.login(dash.email)
    assert visitor.second_step(recovery_code=old[0]).status_code == 401
    assert visitor.second_step(recovery_code=new[0]).status_code == 200
    # A recovery code can ask for a new set too; it is spent with the rest of the old set.
    r = visitor.post(NEW_CODES, {"current_password": PASSWORD, "recovery_code": new[1]})
    assert r.status_code == 200 and set(r.json()["recovery_codes"]).isdisjoint(new)
    assert visitor.get(STATUS).json()["recovery_codes_left"] == 10


# ------------------------------------------------------------ password reset
def test_a_password_reset_does_not_bypass_the_second_factor(dash, new_client, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    anon = new_client()
    assert anon.post("/api/auth/forgot-password", json={"email": dash.email}).json() == {"ok": True}
    token = token_from(last_email(rt, dash.email))
    new_password = "a brand new long password"
    r = anon.post("/api/auth/reset-password", json={"token": token, "password": new_password})
    assert r.status_code == 200 and "set-cookie" not in r.headers  # the link changes the password; it signs nobody in
    assert dash.get("/api/auth/me").status_code == 401  # old sessions are gone, as before
    row = user_row(rt, dash.email)
    assert row.totp_enabled_at is not None and row.totp_secret_enc is not None  # the second factor is untouched
    # Whoever holds the mailbox now has the password, and still not the account.
    intruder = Dash(new_client())
    r = intruder.login(dash.email, new_password)
    assert r.json().get("two_factor_required") is True and intruder.get("/api/auth/me").status_code == 401
    wrong = wrong_code(clock.tick(), secret)
    assert intruder.second_step(code=wrong).status_code == 401
    assert intruder.get("/api/auth/me").status_code == 401
    # The owner, with the phone, gets in.
    owner = Dash(new_client())
    owner.login(dash.email, new_password)
    assert owner.second_step(code=clock.code(secret)).status_code == 200


def test_a_password_reset_does_not_clear_a_lock_earned_by_wrong_codes(dash, new_client, app, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    wrong = wrong_code(clock.tick(), secret)
    for _ in range(8):
        app.state.limiter.reset()
        visitor = Dash(new_client())
        visitor.login(dash.email)
        visitor.second_step(code=wrong)
    assert user_row(rt, dash.email).locked_until is not None
    app.state.limiter.reset()
    anon = new_client()
    anon.post("/api/auth/forgot-password", json={"email": dash.email})
    token = token_from(last_email(rt, dash.email))
    assert (
        anon.post(
            "/api/auth/reset-password", json={"token": token, "password": "a brand new long password"}
        ).status_code
        == 200
    )
    # Otherwise: reset, eight more guesses, reset, eight more guesses...
    assert user_row(rt, dash.email).locked_until is not None
    assert Dash(new_client()).login(dash.email, "a brand new long password").status_code == 429


def test_a_password_reset_still_clears_the_lock_of_an_account_without_two_factor(dash, new_client, app, rt):
    for _ in range(8):
        app.state.limiter.reset()
        Dash(new_client()).login(dash.email, "definitely wrong")
    assert user_row(rt, dash.email).locked_until is not None
    app.state.limiter.reset()
    anon = new_client()
    anon.post("/api/auth/forgot-password", json={"email": dash.email})
    token = token_from(last_email(rt, dash.email))
    anon.post("/api/auth/reset-password", json={"token": token, "password": "a brand new long password"})
    assert Dash(new_client()).login(dash.email, "a brand new long password").status_code == 200


# ---------------------------------------------------------- keys and operator
def test_key_rotation_re_encrypts_the_two_factor_secret(monkeypatch, capsys, rt, dash, new_client, clock):
    secret, _ = dash.enable_two_factor(clock)
    half_way = Dash(new_client())  # a set-up that was started and not confirmed is rotated too
    half_way.signup()
    pending = half_way.post(SETUP, {"current_password": PASSWORD}).json()["secret"]
    assert user_row(rt, dash.email).totp_secret_enc.startswith("v1.t1.")
    new_ring = "t4:" + base64.b64encode(b"q" * 32).decode() + "," + TEST_KEYS
    try:
        code, out, _ = run_cli(monkeypatch, capsys, "rotate-keys", keys=new_ring)
        assert code == 0 and "re-encrypted with key 't4'" in out and "two-factor secret(s)" in out
        row, other = user_row(rt, dash.email), user_row(rt, half_way.email)
        assert row.totp_secret_enc.startswith("v1.t4.") and other.totp_secret_enc.startswith("v1.t4.")
        # The new key alone reads both; the old key alone no longer does.
        only_new = Vault.from_spec(new_ring.split(",")[0])
        assert only_new.decrypt_text(row.totp_secret_enc, aad_totp(row.id)) == secret
        assert only_new.decrypt_text(other.totp_secret_enc, aad_totp(other.id)) == pending
        with pytest.raises(VaultError):
            Vault.from_spec(TEST_KEYS).decrypt_text(row.totp_secret_enc, aad_totp(row.id))
    finally:  # put the suite's key back in front so every other test can still read its data
        assert run_cli(monkeypatch, capsys, "rotate-keys", keys=TEST_KEYS + "," + new_ring.split(",")[0])[0] == 0
    assert user_row(rt, dash.email).totp_secret_enc.startswith("v1.t1.")
    visitor = Dash(new_client())
    visitor.login(dash.email)
    assert visitor.second_step(code=clock.tick().code(secret)).status_code == 200
    assert half_way.post(CONFIRM, {"code": clock.code(pending)}).status_code == 200


def test_key_rotation_reports_a_two_factor_secret_it_cannot_read(monkeypatch, capsys, rt, dash, new_client, clock):
    secret, codes = dash.enable_two_factor(clock)
    row = user_row(rt, dash.email)
    lost = Vault.from_spec("lost:" + base64.b64encode(b"z" * 32).decode())
    with rt.engine.begin() as conn:  # as if written with a key nobody has any more
        conn.execute(
            text("UPDATE users SET totp_secret_enc = :s WHERE id = :u"),
            {"s": lost.encrypt(secret, aad_totp(row.id)), "u": row.id},
        )
    new_ring = "t5:" + base64.b64encode(b"r" * 32).decode() + "," + TEST_KEYS
    try:
        code, out, err = run_cli(monkeypatch, capsys, "rotate-keys", keys=new_ring)
        assert code == 1 and f"two-factor secret of {dash.email}" in err and "disable-two-factor" in err
        assert "you can remove" not in out
        # Codes from the app cannot be checked; that is said plainly, and is not counted as a wrong guess.
        visitor = Dash(new_client())
        visitor.login(dash.email)
        r = visitor.second_step(code=clock.tick().code(secret))
        assert r.status_code == 503 and r.json()["detail"]["code"] == "UNAVAILABLE" and "recovery code" in message(r)
        assert user_row(rt, dash.email).failed_logins == 0
        # A recovery code does not depend on the secret, so there is still a way in.
        assert visitor.second_step(recovery_code=codes[0]).status_code == 200
    finally:
        with rt.engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET totp_secret_enc = :s WHERE id = :u"), {"s": row.totp_secret_enc, "u": row.id}
            )
        assert run_cli(monkeypatch, capsys, "rotate-keys", keys=TEST_KEYS + "," + new_ring.split(",")[0])[0] == 0


def test_a_secret_copied_into_another_account_is_useless_there(dash, new_client, rt, clock):
    secret, _ = dash.enable_two_factor(clock)
    other = Dash(new_client())
    other.signup()
    other.enable_two_factor(clock.tick())
    with rt.engine.begin() as conn:  # someone with write access to the table plants a secret they know
        conn.execute(
            text("UPDATE users SET totp_secret_enc = :s WHERE email = :e"),
            {"s": user_row(rt, dash.email).totp_secret_enc, "e": other.email},
        )
    visitor = Dash(new_client())
    visitor.login(other.email)
    assert visitor.second_step(code=clock.tick().code(secret)).status_code == 503
    assert visitor.get("/api/auth/me").status_code == 401


def test_the_operator_can_switch_it_off_for_someone_who_lost_everything(
    monkeypatch, capsys, rt, dash, new_client, clock
):
    dash.enable_two_factor(clock)
    stuck = Dash(new_client())
    assert "challenge" in stuck.login(dash.email).json()
    code, out, _ = run_cli(monkeypatch, capsys, "disable-two-factor", "--email", dash.email.upper())
    assert code == 0 and f"off for {dash.email}" in out
    row = user_row(rt, dash.email)
    assert (row.totp_secret_enc, row.totp_enabled_at, row.totp_last_step) == (None, None, None)
    assert dash.get("/api/auth/me").status_code == 401  # every session ended
    assert stuck.second_step(code="123456").json()["detail"]["code"] == "CHALLENGE_EXPIRED"
    assert "csrf_token" in stuck.login(dash.email).json()  # the password alone, and it was not changed
    with rt.engine.connect() as conn:
        entry = conn.execute(
            text(
                "SELECT actor_type, actor_label FROM audit_log WHERE action = 'user.two_factor_reset' AND target_id = :u"
            ),
            {"u": row.id},
        ).one()
    assert entry.actor_type == "system" and "operator" in entry.actor_label
    assert "nothing to do" in run_cli(monkeypatch, capsys, "disable-two-factor", "--email", dash.email)[1]
    assert run_cli(monkeypatch, capsys, "disable-two-factor", "--email", "nobody@example.com")[0] == 1


# ---------------------------------------------------------------- migration
def test_the_two_factor_migration_applies_to_a_running_database_and_reverts(settings):
    name = "bdpay_cloud_test_mig_" + uuid.uuid4().hex[:8]
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    url = settings.database_url.rsplit("/", 1)[0] + "/" + name
    new_tables = {"recovery_codes", "login_challenges"}
    new_columns = {"totp_secret_enc", "totp_started_at", "totp_enabled_at", "totp_last_step"}

    def columns(engine):
        return {c["name"] for c in inspect(engine).get_columns("users")}

    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        fresh = create_engine(url)
        migrate.upgrade(url, "0001")  # the previous release, with an account in it
        assert not new_columns & columns(fresh) and not new_tables & set(inspect(fresh).get_table_names())
        insert = "INSERT INTO users (id, email, password_hash) VALUES (:i, :e, 'x')"
        with fresh.begin() as conn:
            conn.execute(text(insert), {"i": "usr_before", "e": "before@example.com"})
        migrate.upgrade(url, "0002")
        assert migrate.current_revision(fresh) == "0002"
        assert new_columns <= columns(fresh) and new_tables <= set(inspect(fresh).get_table_names())
        with fresh.begin() as conn:
            row = conn.execute(text("SELECT * FROM users WHERE id = 'usr_before'")).one()
            assert (row.totp_secret_enc, row.totp_enabled_at, row.totp_last_step) == (None, None, None)
            # The previous release keeps serving during a rolling upgrade: its INSERT still works.
            conn.execute(text(insert), {"i": "usr_during", "e": "during@example.com"})
            conn.execute(
                text("UPDATE users SET totp_secret_enc = 'v1.k.n.c', totp_last_step = 1 WHERE id = 'usr_before'")
            )
            conn.execute(text("INSERT INTO recovery_codes (user_id, code_hash) VALUES ('usr_before', 'h')"))
        migrate.downgrade(url, "0001")
        assert migrate.current_revision(fresh) == "0001"
        assert not new_columns & columns(fresh) and not new_tables & set(inspect(fresh).get_table_names())
        with fresh.connect() as conn:  # accounts survive; only the two-factor data is gone
            assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 2
        migrate.upgrade(url)
        assert migrate.is_current(fresh)
        fresh.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_deleting_an_account_takes_its_two_factor_data_with_it(dash, new_client, rt, clock):
    dash.enable_two_factor(clock)
    Dash(new_client()).login(dash.email)
    uid = dash.me["user"]["id"]
    with rt.engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
        for table in ("recovery_codes", "login_challenges"):
            assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": uid}).scalar() == 0
