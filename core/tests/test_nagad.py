import base64
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from bdpay import Status
from conftest import callback_parts, make_pay


def keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub_der = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return k, priv, base64.b64encode(pub_der).decode()     # Nagad hands out bare base64 keys


def test_full_flow(tmp_path):
    merchant, merchant_priv_pem, merchant_pub_b64 = keypair()
    nagad, _, nagad_pub_b64 = keypair()
    (tmp_path / "m.pem").write_text(merchant_priv_pem)
    (tmp_path / "n.pub").write_text(nagad_pub_b64)
    merchant_pub = merchant.public_key()

    def dec(b64):
        return json.loads(nagad.decrypt(base64.b64decode(b64), padding.PKCS1v15()))

    def check_sig(data, sig):
        merchant_pub.verify(base64.b64decode(sig), json.dumps(data, separators=(",", ":")).encode(),
                            padding.PKCS1v15(), hashes.SHA256())

    def handler(req):
        p = req.url.path
        assert req.headers["x-km-api-version"] == "v-0.2.0" and req.headers["x-km-client-type"] == "PC_WEB"
        if "/check-out/initialize/" in p:
            b = json.loads(req.content)
            s = dec(b["sensitiveData"])
            check_sig(s, b["signature"])
            assert s["merchantId"] == "683002007104225" and s["orderId"] == "INV300" and len(s["datetime"]) == 14
            reply = json.dumps({"paymentReferenceId": "REF1", "challenge": "CH1", "acceptDateTime": s["datetime"]})
            enc = base64.b64encode(merchant_pub.encrypt(reply.encode(), padding.PKCS1v15())).decode()
            sig = base64.b64encode(nagad.sign(reply.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
            return httpx.Response(200, json={"sensitiveData": enc, "signature": sig})
        if "/check-out/complete/REF1" in p:
            b = json.loads(req.content)
            s = dec(b["sensitiveData"])
            check_sig(s, b["signature"])
            assert s == {"merchantId": "683002007104225", "orderId": "INV300", "amount": "99.00",
                         "currencyCode": "050", "challenge": "CH1"}
            return httpx.Response(200, json={"status": "Success", "callBackUrl": "https://nagad.test/pay"})
        if p.endswith("/verify/payment/REF1"):
            return httpx.Response(200, json={"status": "Success", "amount": "99.00", "orderId": "INV300",
                                             "issuerPaymentRefNo": "NG777"})
        return httpx.Response(404)

    pay = make_pay(["nagad"], {"NAGAD_MERCHANT_ID": "683002007104225", "NAGAD_PRIVATE_KEY_PATH": str(tmp_path / "m.pem"),
                               "NAGAD_PUBLIC_KEY_PATH": str(tmp_path / "n.pub")}, handler)
    pay.create_order("INV300", "99", "nagad")
    assert pay.start_payment("INV300") == "https://nagad.test/pay"
    gw, oid, tok, _ = callback_parts(pay.callback_urls(pay.store.get("INV300")).success)
    o = pay.handle_callback(gw, oid, tok, "success", {"payment_ref_id": "REF1", "status": "Success",
                                                       "status_code": "00_0000_000"})
    assert o.status == Status.PAID and o.provider_txn_id == "NG777"
