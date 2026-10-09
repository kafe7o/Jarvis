"""Payments through Stripe: get paid (payment links, invoices), check balance, refund.

Every action that moves money or bills someone asks the user first, every time.
"""

from __future__ import annotations

from datetime import datetime

from ..tools import ToolRegistry, obj
from . import NotConfigured


def _money(amount_minor: int, currency: str) -> str:
    return f"{amount_minor / 100:.2f} {currency.upper()}"


def register(registry: ToolRegistry, ctx) -> None:
    s = ctx.settings

    def stripe():
        if not s.stripe_key:
            raise NotConfigured("Stripe (payments)", ["STRIPE_API_KEY"])
        import stripe as _stripe

        _stripe.api_key = s.stripe_key
        return _stripe

    def minor(amount: float) -> int:
        if amount <= 0:
            raise ValueError("Amount must be positive.")
        return int(round(amount * 100))

    @registry.tool("Show the Stripe account balance (available and pending).", obj({}))
    def payment_balance():
        bal = stripe().Balance.retrieve()
        return {
            "available": [_money(b["amount"], b["currency"]) for b in bal["available"]],
            "pending": [_money(b["amount"], b["currency"]) for b in bal["pending"]],
        }

    @registry.tool("List recent incoming payments.", obj({"limit?": ("integer", "How many (default 10)")}))
    def list_payments(limit: int = 10):
        charges = stripe().Charge.list(limit=limit)
        return [
            {
                "id": c["id"],
                "amount": _money(c["amount"], c["currency"]),
                "status": c["status"],
                "refunded": c["refunded"],
                "description": c.get("description"),
                "email": (c.get("billing_details") or {}).get("email"),
                "date": datetime.fromtimestamp(c["created"]).isoformat(timespec="minutes"),
            }
            for c in charges["data"]
        ]

    @registry.tool(
        "Create a payment link someone can open to pay the owner (card, Apple/Google Pay).",
        obj({
            "amount": ("number", "Amount in major units, e.g. 25.50"),
            "description": ("string", "What the payment is for"),
            "currency?": ("string", "ISO currency, default from JARVIS_CURRENCY"),
        }),
        confirm=True,
        summarize=lambda a: f"ЛИНК ЗА ПЛАЩАНЕ: {a.get('amount')} {(a.get('currency') or s.currency).upper()} за „{a.get('description')}“",
    )
    def create_payment_link(amount: float, description: str, currency: str | None = None):
        st = stripe()
        price = st.Price.create(
            currency=currency or s.currency, unit_amount=minor(amount), product_data={"name": description}
        )
        link = st.PaymentLink.create(line_items=[{"price": price["id"], "quantity": 1}])
        return f"Payment link: {link['url']}"

    @registry.tool(
        "Bill a customer: create and e-mail a Stripe invoice.",
        obj({
            "email": ("string", "Customer e-mail"),
            "amount": ("number", "Amount in major units"),
            "description": ("string", "What it is for"),
            "name?": ("string", "Customer name"),
            "days_until_due?": ("integer", "Days to pay (default 7)"),
            "currency?": ("string", "ISO currency"),
        }),
        confirm=True,
        summarize=lambda a: f"ФАКТУРА до {a.get('email')}: {a.get('amount')} {(a.get('currency') or s.currency).upper()} за „{a.get('description')}“",
    )
    def send_invoice(email: str, amount: float, description: str, name: str | None = None,
                     days_until_due: int = 7, currency: str | None = None):
        st = stripe()
        found = st.Customer.list(email=email, limit=1)["data"]
        customer = found[0] if found else st.Customer.create(email=email, name=name)
        invoice = st.Invoice.create(
            customer=customer["id"], collection_method="send_invoice", days_until_due=days_until_due,
            currency=currency or s.currency,
        )
        st.InvoiceItem.create(
            customer=customer["id"], invoice=invoice["id"], amount=minor(amount),
            currency=currency or s.currency, description=description,
        )
        st.Invoice.finalize_invoice(invoice["id"])
        sent = st.Invoice.send_invoice(invoice["id"])
        return f"Invoice sent to {email}: {sent.get('hosted_invoice_url')}"

    @registry.tool(
        "Refund a payment, fully or partly.",
        obj({"charge_id": ("string", "Charge id from list_payments"), "amount?": ("number", "Partial amount; omit for full")}),
        confirm=True,
        summarize=lambda a: f"ВЪЗСТАНОВЯВАНЕ на плащане {a.get('charge_id')}" + (f", сума {a['amount']}" if a.get("amount") else " (цялата сума)"),
    )
    def refund_payment(charge_id: str, amount: float | None = None):
        kwargs = {"charge": charge_id}
        if amount:
            kwargs["amount"] = minor(amount)
        refund = stripe().Refund.create(**kwargs)
        return f"Refund {refund['id']}: {refund['status']}, {_money(refund['amount'], refund['currency'])}."

    @registry.tool(
        "Pay out the Stripe balance to the owner's bank account.",
        obj({"amount": ("number", "Amount in major units"), "currency?": ("string", "ISO currency")}),
        confirm=True,
        summarize=lambda a: f"ИЗПЛАЩАНЕ към банковата ти сметка: {a.get('amount')} {(a.get('currency') or s.currency).upper()}",
    )
    def payout(amount: float, currency: str | None = None):
        p = stripe().Payout.create(amount=minor(amount), currency=currency or s.currency)
        return f"Payout {p['id']}: {p['status']}, arrives {datetime.fromtimestamp(p['arrival_date']).date()}."
