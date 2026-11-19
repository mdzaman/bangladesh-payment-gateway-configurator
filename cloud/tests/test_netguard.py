import socket

import httpx
import pytest

from bdpay.core import BdPayError
from bdpay_cloud.engine.http import CloudHttp
from bdpay_cloud.security import netguard
from bdpay_cloud.security.netguard import (
    BlockedAddress,
    GuardedBackend,
    GuardedTransport,
    check_public_url,
    is_public_ip,
    resolve_checked,
)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::1",
        "fe80::1%en0",
        "not-an-ip",
        "",
    ],
)
def test_non_public_addresses_are_refused(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "203.112.218.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(ip):
    assert is_public_ip(ip)


def test_resolve_refuses_if_any_address_is_private(monkeypatch):
    def fake(host, port, type=0):  # one public and one private A record
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port)),
        ]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake)
    with pytest.raises(BlockedAddress):
        resolve_checked("rebind.example", 443)
    assert resolve_checked("rebind.example", 443, allow_private=True) == ["93.184.216.34", "10.0.0.7"]


def test_ipv4_is_preferred(monkeypatch):
    def fake(host, port, type=0):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port)),
        ]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake)
    assert resolve_checked("dual.example", 443) == ["1.1.1.1", "2606:4700:4700::1111"]


@pytest.mark.parametrize(
    "url,ok_in_sandbox,ok_in_live",
    [
        ("https://8.8.8.8/hook", True, True),
        ("http://8.8.8.8/hook", True, False),
        ("https://127.0.0.1/hook", False, False),
        ("https://169.254.169.254/latest/meta-data", False, False),
        ("https://[::1]/hook", False, False),
        ("ftp://8.8.8.8/x", False, False),
        ("https://user:pw@8.8.8.8/x", False, False),
        ("javascript:alert(1)", False, False),
        ("", False, False),
    ],
)
def test_check_public_url(url, ok_in_sandbox, ok_in_live):
    for allow_http, expected in ((True, ok_in_sandbox), (False, ok_in_live)):
        if expected:
            assert check_public_url(url, allow_http=allow_http) == url
        else:
            with pytest.raises(ValueError):
                check_public_url(url, allow_http=allow_http)


def test_unresolvable_host_is_accepted_at_save_time_but_private_is_not():
    assert check_public_url("https://does-not-exist.invalid/x", allow_http=False)
    assert check_public_url("http://localhost:9000/x", allow_http=True, allow_private=True)
    with pytest.raises(ValueError):
        check_public_url("http://localhost:9000/x", allow_http=True)


def test_transport_really_uses_the_guard():
    transport = GuardedTransport()
    # If httpx ever changes how it holds its pool, this is the test that says so.
    assert isinstance(transport._pool._network_backend, GuardedBackend)
    with httpx.Client(transport=transport, timeout=2) as client:
        for url in ("http://127.0.0.1:9/", "http://[::1]:9/", "http://169.254.169.254/", "http://localhost:9/"):
            with pytest.raises(httpx.ConnectError) as e:
                client.get(url)
            assert "private, local or reserved" in str(e.value)


def test_provider_client_refuses_private_targets_and_plain_http_in_live():
    with pytest.raises(BdPayError) as e:
        CloudHttp("sandbox").request("upay", "GET", "http://127.0.0.1:9/payment/merchant-auth/")
    assert e.value.code == "PROVIDER_UNAVAILABLE"
    with pytest.raises(BdPayError) as e:
        CloudHttp("live").request("upay", "GET", "http://8.8.8.8/")
    assert e.value.code == "CONFIG"


def test_private_targets_can_be_allowed_for_local_development():
    backend = GuardedBackend(allow_private=True)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        stream = backend.connect_tcp("127.0.0.1", server.getsockname()[1], timeout=2)
        stream.close()
    finally:
        server.close()
