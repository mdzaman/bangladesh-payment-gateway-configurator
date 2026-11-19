import base64

import pytest

from bdpay_cloud.crypto import Vault, VaultError, aad_gateway, generate_key_entry, parse_key_ring
from bdpay_cloud.settings import DEV_MASTER_KEY


def key(byte: bytes) -> str:
    return base64.b64encode(byte * 32).decode()


def test_round_trip_and_format():
    v = Vault.from_spec(f"k1:{key(b'a')}")
    token = v.encrypt("app-secret-value", "gateway:ws_1:sandbox:bkash")
    assert token.startswith("v1.k1.") and "app-secret-value" not in token
    assert v.decrypt_text(token, "gateway:ws_1:sandbox:bkash") == "app-secret-value"
    assert v.encrypt("x", "a") != v.encrypt("x", "a")  # fresh nonce every time


def test_ciphertext_is_bound_to_its_row():
    v = Vault.from_spec(f"k1:{key(b'a')}")
    token = v.encrypt_json({"BKASH_APP_SECRET": "s"}, aad_gateway("ws_a", "sandbox", "bkash"))
    assert v.decrypt_json(token, aad_gateway("ws_a", "sandbox", "bkash")) == {"BKASH_APP_SECRET": "s"}
    for other in (
        aad_gateway("ws_b", "sandbox", "bkash"),
        aad_gateway("ws_a", "live", "bkash"),
        aad_gateway("ws_a", "sandbox", "nagad"),
    ):
        with pytest.raises(VaultError):
            v.decrypt_json(token, other)  # copied into another tenant/mode/gateway


def test_tampering_is_detected():
    v = Vault.from_spec(f"k1:{key(b'a')}")
    token = v.encrypt("secret", "ctx")
    head, kid, nonce, ct = token.split(".")
    flipped = ct[:-2] + ("A" if ct[-2] != "A" else "B") + ct[-1]
    with pytest.raises(VaultError):
        v.decrypt(".".join([head, kid, nonce, flipped]), "ctx")
    with pytest.raises(VaultError):
        v.decrypt("not-a-token", "ctx")


def test_rotation():
    old = Vault.from_spec(f"k1:{key(b'a')}")
    token = old.encrypt("secret", "ctx")
    ring = Vault.from_spec(f"k2:{key(b'b')},k1:{key(b'a')}")  # new key first, old key still readable
    assert ring.primary == "k2" and ring.needs_rotation(token)
    rotated = ring.rotate(token, "ctx")
    assert rotated.startswith("v1.k2.") and not ring.needs_rotation(rotated)
    only_new = Vault.from_spec(f"k2:{key(b'b')}")
    assert only_new.decrypt_text(rotated, "ctx") == "secret"
    with pytest.raises(VaultError):
        only_new.decrypt(token, "ctx")  # old key removed too early


def test_key_ring_validation():
    with pytest.raises(VaultError):
        parse_key_ring("")
    with pytest.raises(VaultError):
        parse_key_ring("k1:" + base64.b64encode(b"short").decode())
    with pytest.raises(VaultError):
        parse_key_ring("no-colon")
    with pytest.raises(VaultError):
        parse_key_ring(f"k1:{key(b'a')},k1:{key(b'b')}")
    assert list(parse_key_ring(generate_key_entry("k9"))) == ["k9"]
    assert len(parse_key_ring(DEV_MASTER_KEY)["dev"]) == 32


def test_production_refuses_missing_or_published_key():
    from bdpay_cloud.settings import ConfigError

    from conftest import make_settings

    s = make_settings(env="production", master_keys="", public_url="https://pay.example.com", mail_backend="smtp")
    assert any("MASTER_KEYS" in p for p in s.problems())
    with pytest.raises(ConfigError):
        _ = s.effective_master_keys
    s = make_settings(
        env="production", master_keys=DEV_MASTER_KEY, public_url="https://pay.example.com", mail_backend="smtp"
    )
    assert any("development key" in p for p in s.problems())
    s = make_settings(env="production", public_url="http://pay.example.com", mail_backend="smtp")
    assert any("https" in p for p in s.problems())
    s = make_settings(
        env="production", public_url="https://pay.example.com", mail_backend="smtp", allow_private_targets=True
    )
    assert any("PRIVATE_TARGETS" in p for p in s.problems())
    assert make_settings(env="production", public_url="https://pay.example.com", mail_backend="smtp").problems() == []
