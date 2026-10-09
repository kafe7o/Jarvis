"""Paying other people: PayPal payouts (to any e-mail or phone with PayPal) and Wise transfers
(to a bank account / IBAN). Every payment asks the owner first, with recipient and amount.

PayPal: PAYPAL_CLIENT_ID + PAYPAL_SECRET from developer.paypal.com (a business account with
Payouts enabled). Wise: WISE_API_TOKEN from wise.com > Settings > API tokens; in the EU Wise may
additionally ask you to approve the transfer in its app (strong customer authentication).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
import uuid

from ..tools import ToolRegistry, obj
from . import NotConfigured


def _http(url: str, body=None, headers=None, form: bool = False):
    data = None
    if body is not None:
        data = body.encode() if form else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(headers or {})},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"null")


def paypal_token() -> tuple[str, str]:
    cid, secret = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_SECRET")
    if not (cid and secret):
        raise NotConfigured("PayPal payouts", ["PAYPAL_CLIENT_ID", "PAYPAL_SECRET"])
    base = "https://api-m.sandbox.paypal.com" if os.environ.get("PAYPAL_SANDBOX") == "1" else "https://api-m.paypal.com"
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    tok = _http(f"{base}/v1/oauth2/token", "grant_type=client_credentials", {"Authorization": f"Basic {auth}"}, form=True)
    return base, tok["access_token"]


def register(registry: ToolRegistry, ctx) -> None:
    s = ctx.settings

    @registry.tool(
        "Send money with PayPal to someone's e-mail or phone number.",
        obj({
            "to": ("string", "Recipient PayPal e-mail or phone"),
            "amount": ("number", "Amount in major units"),
            "currency?": ("string", "ISO currency (default JARVIS_CURRENCY)"),
            "note?": ("string", "Message to the recipient"),
        }),
        confirm=True,
        summarize=lambda a: f"ПЛАЩАНЕ PayPal: {a.get('amount')} {(a.get('currency') or s.currency).upper()} на {a.get('to')}"
        + (f" („{a['note']}“)" if a.get("note") else ""),
    )
    def paypal_send(to: str, amount: float, currency: str | None = None, note: str | None = None):
        if amount <= 0:
            raise ValueError("Amount must be positive.")
        base, token = paypal_token()
        kind = "EMAIL" if "@" in to else "PHONE"
        res = _http(f"{base}/v1/payments/payouts", {
            "sender_batch_header": {"sender_batch_id": uuid.uuid4().hex, "email_subject": "Плащане"},
            "items": [{
                "recipient_type": kind, "receiver": to, "note": note or "",
                "amount": {"value": f"{amount:.2f}", "currency": (currency or s.currency).upper()},
            }],
        }, {"Authorization": f"Bearer {token}"})
        return f"PayPal payout {res['batch_header']['payout_batch_id']}: {res['batch_header']['batch_status']}"

    @registry.tool(
        "Send a bank transfer with Wise to an IBAN.",
        obj({
            "name": ("string", "Recipient full name"),
            "iban": ("string", "Recipient IBAN"),
            "amount": ("number", "Amount the recipient gets"),
            "currency?": ("string", "Recipient currency (default JARVIS_CURRENCY)"),
            "reference?": ("string", "Payment reference"),
        }),
        confirm=True,
        summarize=lambda a: f"БАНКОВ ПРЕВОД (Wise): {a.get('amount')} {(a.get('currency') or s.currency).upper()} "
        f"на {a.get('name')}, IBAN {a.get('iban')}" + (f", основание „{a['reference']}“" if a.get("reference") else ""),
    )
    def bank_transfer(name: str, iban: str, amount: float, currency: str | None = None, reference: str | None = None):
        token = os.environ.get("WISE_API_TOKEN")
        if not token:
            raise NotConfigured("Wise transfers", ["WISE_API_TOKEN"])
        api = "https://api.wise.com"
        auth = {"Authorization": f"Bearer {token}"}
        cur = (currency or s.currency).upper()
        profile = next(p for p in _http(f"{api}/v2/profiles", headers=auth) if p["type"].lower() == "personal")
        quote = _http(f"{api}/v3/profiles/{profile['id']}/quotes",
                      {"sourceCurrency": cur, "targetCurrency": cur, "targetAmount": amount}, auth)
        recipient = _http(f"{api}/v1/accounts", {
            "profile": profile["id"], "accountHolderName": name, "currency": cur, "type": "iban",
            "details": {"legalType": "PRIVATE", "iban": iban.replace(" ", "")},
        }, auth)
        transfer = _http(f"{api}/v1/transfers", {
            "targetAccount": recipient["id"], "quoteUuid": quote["id"], "customerTransactionId": str(uuid.uuid4()),
            "details": {"reference": reference or ""},
        }, auth)
        funded = _http(f"{api}/v3/profiles/{profile['id']}/transfers/{transfer['id']}/payments", {"type": "BALANCE"}, auth)
        return f"Transfer {transfer['id']}: {funded.get('status')}"
