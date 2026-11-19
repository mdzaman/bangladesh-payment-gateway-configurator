import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bdpay import BdPay, Settings  # noqa: E402
from bdpay.store import Store  # noqa: E402


def make_pay(gateways, env_extra, handler):
    env = {"BDPAY_ENV": "sandbox", "BDPAY_GATEWAYS": ",".join(gateways), "BDPAY_SECRET": "s" * 40,
           "BDPAY_PUBLIC_URL": "https://shop.test", **env_extra}
    return BdPay(Settings.from_env(env), store=Store(":memory:"), transport=httpx.MockTransport(handler))


def callback_parts(url):
    """https://shop.test/bdpay/callback/<gw>/<order>/<token>/<outcome>?a=b -> (gw, order, token, outcome)"""
    p = urlparse(url).path.split("/")
    i = p.index("callback")
    return p[i + 1], p[i + 2], p[i + 3], p[i + 4]


def body(request: httpx.Request) -> dict:
    raw = request.content.decode() if request.content else ""
    if not raw:
        return {}
    if "json" in request.headers.get("content-type", ""):
        return json.loads(raw)
    return {k: v[0] for k, v in parse_qs(raw).items()}
