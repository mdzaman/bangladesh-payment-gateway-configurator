"""Ready-to-run payment service + browser test console.

    pip install -r requirements.txt
    python -m bdpay doctor
    uvicorn app:app --reload        # open http://localhost:8000
"""
import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from bdpay import BdPay
from bdpay.web import build_router

logging.basicConfig(level=os.environ.get("BDPAY_LOG_LEVEL", "INFO"))

pay = BdPay.from_env()


def on_paid(order):
    # >>> Put your fulfilment here: mark the invoice paid, send the receipt, ship the goods.
    logging.getLogger("app").info("PAID %s %s BDT via %s", order.order_id, order.amount, order.gateway)


app = FastAPI(title="bdpay", version="1.0.0")
app.include_router(build_router(pay, on_paid=on_paid, result_url=os.environ.get("BDPAY_RESULT_URL") or None,
                                admin_token=os.environ.get("BDPAY_ADMIN_TOKEN") or None))


@app.get("/health")
def health():
    return {"ok": True, "environment": pay.settings.environment, "gateways": list(pay.drivers)}


CONSOLE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>bdpay test console</title>
<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#1d2433}
.banner{padding:10px 14px;border-radius:8px;font-weight:600;margin-bottom:18px}
.sandbox{background:#e7f6ec;color:#0a6b34}.live{background:#b42318;color:#fff}
label{display:block;margin:12px 0 4px;font-weight:600}input,select{width:100%;padding:10px;border:1px solid #c9ced8;border-radius:8px;font-size:15px;box-sizing:border-box}
button{margin-top:18px;padding:12px 18px;border:0;border-radius:8px;background:#1f5eff;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
pre{background:#f4f6fa;padding:12px;border-radius:8px;overflow:auto;font-size:13px}</style></head><body>
<div class="banner __ENV__">__ENVTEXT__</div>
<h1>bdpay test console</h1>
<label>Gateway</label><select id=gw></select>
<label>Amount (BDT)</label><input id=amt value="10.00">
<label>Order ID (letters and digits)</label><input id=oid>
<label>Customer phone</label><input id=phone value="01700000000">
<label>Admin token (only if BDPAY_ADMIN_TOKEN is set)</label><input id=adm type=password>
<button onclick="go()">Create order and pay</button>
<pre id=out></pre>
<script>
const $=id=>document.getElementById(id);$("oid").value="INV"+Date.now();
fetch("/bdpay/gateways").then(r=>r.json()).then(g=>{$("gw").innerHTML=g.map(x=>`<option value="${x.id}">${x.label} (${x.stability})</option>`).join("")});
async function call(url,body){const r=await fetch(url,{method:"POST",headers:{"content-type":"application/json","x-bdpay-admin":$("adm").value},body:JSON.stringify(body||{})});const j=await r.json();if(!r.ok)throw j;return j}
async function go(){$("out").textContent="Working...";try{
 const o=await call("/bdpay/orders",{order_id:$("oid").value,amount:$("amt").value,gateway:$("gw").value,customer:{phone:$("phone").value,name:"Test Customer"}});
 const p=await call(`/bdpay/orders/${o.order_id}/pay`);$("out").textContent="Redirecting to "+p.redirect_url;location.href=p.redirect_url;
}catch(e){$("out").textContent=JSON.stringify(e,null,2)}}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def console():
    live = pay.settings.environment == "live"
    if live and os.environ.get("BDPAY_CONSOLE") != "1":
        return HTMLResponse("<h1>bdpay is running (live). Test console is off.</h1>")
    return CONSOLE.replace("__ENV__", "live" if live else "sandbox").replace(
        "__ENVTEXT__", "LIVE MODE: real money" if live else "Sandbox mode: no real money moves")
