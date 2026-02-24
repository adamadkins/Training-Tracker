"""Stripe webhook and billing helpers. Webhook is public (no auth); checkout/portal creation is in admin."""
import stripe
from flask import Blueprint, request, current_app, abort

from app import db
from app.models import Organization


def _billing_plan_from_price_id(price_id):
    if not price_id:
        return None
    pro_id = current_app.config.get("STRIPE_PRICE_ID_PRO") or ""
    std_id = current_app.config.get("STRIPE_PRICE_ID_STANDARD") or current_app.config.get("STRIPE_PRICE_ID") or ""
    if price_id == pro_id:
        return "pro"
    if price_id == std_id:
        return "standard"
    return None

billing_bp = Blueprint("billing", __name__, url_prefix="/webhooks")


@billing_bp.route("/stripe", methods=["GET"])
def stripe_webhook_get():
    """Browser-friendly response; Stripe sends POST only."""
    return "Stripe webhook endpoint. Use POST (e.g. from Stripe Dashboard or Send test webhook).", 200


@billing_bp.route("/stripe", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events. Must use raw body for signature verification."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET") or ""
    if not secret:
        current_app.logger.warning("STRIPE_WEBHOOK_SECRET not set; skipping webhook verification")
        return "", 200

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError as e:
        current_app.logger.warning(f"Stripe webhook invalid payload: {e}")
        abort(400)
    except stripe.SignatureVerificationError as e:
        current_app.logger.warning(f"Stripe webhook signature error: {e}")
        abort(400)

    typ = event.get("type")
    if typ == "checkout.session.completed":
        _handle_checkout_completed(event["data"]["object"])
    elif typ == "customer.subscription.updated":
        _handle_subscription_updated(event["data"]["object"])
    elif typ == "customer.subscription.deleted":
        _handle_subscription_deleted(event["data"]["object"])
    elif typ == "invoice.payment_failed":
        _handle_invoice_payment_failed(event["data"]["object"])

    return "", 200


def _handle_checkout_completed(session):
    """Link Stripe customer and subscription to organization from Checkout Session."""
    org_id = session.get("client_reference_id")
    if not org_id:
        return
    try:
        org_id = int(org_id)
    except (TypeError, ValueError):
        return
    org = Organization.query.get(org_id)
    if not org:
        return
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    if customer_id:
        org.stripe_customer_id = customer_id
    if subscription_id:
        org.stripe_subscription_id = subscription_id
    if not org.stripe_customer_id and subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            if sub.customer:
                org.stripe_customer_id = sub.customer
        except Exception:
            pass
    if subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            org.stripe_subscription_status = sub.get("status")
            items = sub.get("items", {}).get("data", [])
            if items:
                price = items[0].get("price")
                price_id = price.get("id") if isinstance(price, dict) else price
                if price_id:
                    org.billing_plan = _billing_plan_from_price_id(price_id)
        except Exception:
            pass
    db.session.commit()


def _handle_subscription_updated(subscription):
    """Optionally sync subscription status; suspend org if subscription past_due/canceled."""
    sub_id = subscription.get("id")
    customer_id = subscription.get("customer")
    status = subscription.get("status")
    org = Organization.query.filter(
        (Organization.stripe_subscription_id == sub_id) | (Organization.stripe_customer_id == customer_id)
    ).first()
    if not org:
        return
    org.stripe_subscription_id = sub_id
    org.stripe_subscription_status = status
    if customer_id:
        org.stripe_customer_id = customer_id
    items = subscription.get("items", {}).get("data", [])
    if items:
        price = items[0].get("price")
        price_id = price.get("id") if isinstance(price, dict) else price
        if price_id:
            org.billing_plan = _billing_plan_from_price_id(price_id)
    if status in ("past_due", "canceled", "unpaid", "incomplete_expired"):
        org.status = "suspended"
    db.session.commit()


def _handle_subscription_deleted(subscription):
    """Clear subscription id; optionally suspend org."""
    sub_id = subscription.get("id")
    org = Organization.query.filter_by(stripe_subscription_id=sub_id).first()
    if not org:
        return
    org.stripe_subscription_id = None
    org.stripe_subscription_status = "canceled"
    org.billing_plan = None
    org.status = "suspended"
    db.session.commit()


def _handle_invoice_payment_failed(invoice):
    """Optionally suspend org when payment fails (dunning)."""
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    org = Organization.query.filter_by(stripe_customer_id=customer_id).first()
    if not org:
        return
    org.status = "suspended"
    db.session.commit()
