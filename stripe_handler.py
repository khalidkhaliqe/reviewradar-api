"""
Stripe subscription management.
Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET environment variables.
"""
import os
import stripe
from sqlalchemy.orm import Session
from models import User

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_REPLACE_ME")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_REPLACE_ME")

# Price IDs — create these in your Stripe dashboard
PRICE_IDS = {
    "starter": os.getenv("STRIPE_STARTER_PRICE_ID", "price_starter_REPLACE_ME"),
    "pro": os.getenv("STRIPE_PRO_PRICE_ID", "price_pro_REPLACE_ME"),
}

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def create_checkout_session(user: User, plan: str) -> str:
    if plan not in PRICE_IDS:
        raise ValueError(f"Invalid plan: {plan}")

    # Create or reuse Stripe customer
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(
            email=user.email,
            name=user.business_name,
            metadata={"user_id": str(user.id)},
        )
        customer_id = customer.id
    else:
        customer_id = user.stripe_customer_id

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card", "ideal"],
        line_items=[{"price": PRICE_IDS[plan], "quantity": 1}],
        mode="subscription",
        success_url=f"{FRONTEND_URL}/dashboard.html?payment=success",
        cancel_url=f"{FRONTEND_URL}/dashboard.html?payment=cancelled",
        metadata={"user_id": str(user.id), "plan": plan},
        allow_promotion_codes=True,
        subscription_data={"trial_period_days": 14},
    )
    return session.url


def create_portal_session(user: User) -> str:
    if not user.stripe_customer_id:
        raise ValueError("No Stripe customer found")

    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=f"{FRONTEND_URL}/dashboard.html",
    )
    return session.url


def handle_webhook_event(payload: bytes, sig_header: str, db: Session) -> dict:
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise ValueError("Invalid webhook signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = int(session["metadata"]["user_id"])
        plan = session["metadata"]["plan"]
        customer_id = session["customer"]
        subscription_id = session["subscription"]

        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.plan = plan
            user.stripe_customer_id = customer_id
            user.stripe_subscription_id = subscription_id
            db.commit()

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.plan = "free"
            user.stripe_subscription_id = None
            db.commit()

    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user and subscription["status"] != "active":
            user.plan = "free"
            db.commit()

    return {"status": "ok"}
