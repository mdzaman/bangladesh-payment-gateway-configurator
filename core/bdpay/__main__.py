"""Command line: python -m bdpay <doctor|reconcile|serve|requery ORDER_ID>"""
from __future__ import annotations

import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .config import Settings
from .core import BdPayError


def doctor() -> int:
    ok = True
    try:
        s = Settings.from_env()
    except BdPayError as e:
        print("✗", e)
        return 1
    print(f"environment : {s.environment}{'   <<< LIVE: real money >>>' if s.environment == 'live' else ''}")
    print(f"gateways    : {', '.join(s.gateways) or '(none) set BDPAY_GATEWAYS'}")
    print(f"public url  : {s.public_url}")
    if s.environment == "live" and not s.public_url.startswith("https://"):
        print("✗ BDPAY_PUBLIC_URL must be https:// in live mode"); ok = False
    if "localhost" in s.public_url or "127.0.0.1" in s.public_url:
        print("! public url is localhost: providers cannot reach your callback/IPN. Use ngrok or a real domain.")
    dhaka = datetime.now(timezone(timedelta(hours=6)))
    print(f"Dhaka time  : {dhaka:%Y-%m-%d %H:%M:%S} (Nagad/Upay timestamps use this)")
    from .registry import load_driver_class
    from .http import Http
    from .store import Store, TokenCache
    store = Store(":memory:")
    for name in s.gateways:
        try:
            cls = load_driver_class(name)
            d = cls(s.gateway(name), Http(s.environment), TokenCache(store))
            host = next((getattr(d, a) for a in ("url", "host", "base") if hasattr(d, a)), "")
            h = urlparse(host).hostname
            if h:
                t = time.monotonic()
                socket.create_connection((h, urlparse(host).port or (443 if host.startswith("https") else 80)), 5).close()
                print(f"✓ {name:<11} config ok, {h} reachable ({int((time.monotonic() - t) * 1000)} ms) [{d.stability}]")
            else:
                print(f"✓ {name:<11} config ok [{d.stability}]")
        except BdPayError as e:
            ok = False; print(f"✗ {name:<11} {e.message}")
        except OSError as e:
            ok = False; print(f"✗ {name:<11} network: {e}")
    try:
        ip = socket.gethostbyname(socket.gethostname())
        print(f"host ip     : {ip} (Nagad and Upay whitelist your public outbound IPv4, which may differ)")
    except OSError:
        pass
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "doctor"
    if cmd == "doctor":
        return doctor()
    if cmd == "reconcile":
        from .service import BdPay
        for order_id, status in BdPay.from_env().reconcile():
            print(order_id, status)
        return 0
    if cmd == "requery" and len(argv) > 1:
        from .service import BdPay
        o = BdPay.from_env().requery(argv[1])
        print(o.order_id, o.status.value)
        return 0
    if cmd == "serve":
        import uvicorn
        uvicorn.run("app:app", host="0.0.0.0", port=int(argv[1]) if len(argv) > 1 else 8000)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
