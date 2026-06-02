"""Payment integration — placeholder for WeChat Pay & Alipay.

Status machine:
    pending → paid → activated
         └→ cancelled / expired

This module provides the business logic layer; HTTP routes are in
roundtable.routers.payment.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

from roundtable import db
from roundtable.billing import PlanTier, get_plan_limits

logger = logging.getLogger("roundtable.payment")


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    ACTIVATED = "activated"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REFUNDED = "refunded"


# ── Order creation ──

def create_order(user_id: str, plan: str, provider: str = "") -> dict:
    """Create a new payment order.

    Args:
        user_id: The subscribing user.
        plan: One of 'pro' or 'team'.
        provider: 'wechat' | 'alipay' | 'stripe' | '' (auto-detect later).

    Returns:
        Order dict with order_id and amount_cents.
    """
    if plan not in {PlanTier.PRO.value, PlanTier.TEAM.value}:
        raise ValueError(f"Invalid plan for payment: {plan}")

    limits = get_plan_limits(plan)
    amount_cents = limits["price_cents_monthly"]
    order_id = f"ord_{uuid.uuid4().hex}"

    db.insert_order(
        order_id=order_id,
        user_id=user_id,
        plan=plan,
        amount_cents=amount_cents,
        currency="CNY",
        provider=provider or "wechat",
        metadata={"created_by": "payment.create_order"},
    )
    logger.info("Created order %s for user=%s plan=%s amount=%s", order_id, user_id, plan, amount_cents)
    return {
        "order_id": order_id,
        "user_id": user_id,
        "plan": plan,
        "amount_cents": amount_cents,
        "currency": "CNY",
        "provider": provider or "wechat",
        "status": OrderStatus.PENDING.value,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Provider-specific placeholders ──

def _create_wechat_order(order_id: str, amount_cents: int, description: str) -> dict:
    """Placeholder: call WeChat Pay unified order API."""
    # In production, use wechatpayv3 or similar SDK
    logger.info("[PLACEHOLDER] WeChat Pay order %s amount=%s", order_id, amount_cents)
    return {
        "prepay_id": f"wx_{uuid.uuid4().hex[:16]}",
        "app_id": "wx_placeholder",
        "nonce_str": uuid.uuid4().hex[:16],
        "timestamp": str(int(datetime.now(timezone.utc).timestamp())),
        "package": f"prepay_id=wx_{uuid.uuid4().hex[:16]}",
        "sign": "placeholder_sign",
        "sign_type": "RSA",
    }


def _create_alipay_order(order_id: str, amount_cents: int, description: str) -> dict:
    """Placeholder: call Alipay trade create API."""
    logger.info("[PLACEHOLDER] Alipay order %s amount=%s", order_id, amount_cents)
    return {
        "trade_no": f"ali_{uuid.uuid4().hex[:16]}",
        "out_trade_no": order_id,
        "total_amount": f"{amount_cents / 100:.2f}",
        "qr_code": f"https://example.com/qr/{order_id}",
    }


def prepare_payment(order_id: str) -> dict:
    """Generate payment params for the frontend SDK.

    Returns provider-specific payload (prepay params for WeChat, QR for Alipay).
    """
    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    if order["status"] != OrderStatus.PENDING.value:
        raise ValueError(f"Order {order_id} is not pending (status={order['status']})")

    provider = order.get("provider", "wechat")
    amount_cents = order["amount_cents"]
    description = f"Roundtable {order['plan']} plan"

    if provider == "wechat":
        payload = _create_wechat_order(order_id, amount_cents, description)
    elif provider == "alipay":
        payload = _create_alipay_order(order_id, amount_cents, description)
    else:
        payload = {"message": "Provider not yet implemented"}

    return {
        "order_id": order_id,
        "provider": provider,
        "amount_cents": amount_cents,
        "currency": order.get("currency", "CNY"),
        "payload": payload,
    }


# ── Callback / Verification ──

def verify_wechat_signature(body: bytes, signature: str) -> bool:
    """Placeholder: verify WeChat Pay callback signature."""
    # TODO: load WeChat mch API cert and verify RSA signature
    logger.warning("WeChat signature verification is a placeholder — always returns True in dev")
    return True


def verify_alipay_signature(params: dict, signature: str) -> bool:
    """Placeholder: verify Alipay callback signature."""
    # TODO: load Alipay public key and verify RSA signature
    logger.warning("Alipay signature verification is a placeholder — always returns True in dev")
    return True


def handle_payment_callback(
    provider: str,
    payload: dict,
    signature: str = "",
) -> dict:
    """Process payment provider callback.

    Args:
        provider: 'wechat' | 'alipay'.
        payload: Provider-specific notification body.
        signature: Signature header for verification.

    Returns:
        Result dict with order_id and new status.
    """
    if provider == "wechat":
        # WeChat notification format
        order_id = payload.get("out_trade_no", "")
        provider_order_id = payload.get("transaction_id", "")
        paid = payload.get("result_code", "") == "SUCCESS"
    elif provider == "alipay":
        order_id = payload.get("out_trade_no", "")
        provider_order_id = payload.get("trade_no", "")
        paid = payload.get("trade_status", "") in ("TRADE_SUCCESS", "TRADE_FINISHED")
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    if not order_id:
        raise ValueError("Missing order_id in callback payload")

    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")

    if order["status"] != OrderStatus.PENDING.value:
        logger.info("Order %s already processed (status=%s)", order_id, order["status"])
        return {"order_id": order_id, "status": order["status"], "message": "already processed"}

    if not paid:
        logger.warning("Payment failed for order %s: %s", order_id, payload)
        return {"order_id": order_id, "status": OrderStatus.PENDING.value, "message": "payment not successful"}

    # Verify signature (placeholder in dev)
    if provider == "wechat":
        verify_wechat_signature(b"", signature)
    elif provider == "alipay":
        verify_alipay_signature(payload, signature)

    # Mark paid + activate
    now = datetime.now(timezone.utc).isoformat()
    db.update_order_status(
        order_id=order_id,
        status=OrderStatus.PAID.value,
        provider_order_id=provider_order_id,
        paid_at=now,
    )
    logger.info("Order %s marked as PAID (provider_order_id=%s)", order_id, provider_order_id)

    # Activate subscription
    _activate_subscription(order_id, order["user_id"], order["plan"])

    return {
        "order_id": order_id,
        "status": OrderStatus.ACTIVATED.value,
        "plan": order["plan"],
        "activated_at": now,
    }


def _activate_subscription(order_id: str, user_id: str, plan: str) -> None:
    """Upgrade user plan after successful payment."""
    limits = get_plan_limits(plan)
    now = datetime.now(timezone.utc)
    next_month = now + timedelta(days=30)

    db.update_user_plan(
        user_id=user_id,
        plan=plan,
        subscription_status="active",
        quota_reset_at=next_month.isoformat(),
        monthly_quota=limits["monthly_quota"],
    )
    db.update_order_status(
        order_id=order_id,
        status=OrderStatus.ACTIVATED.value,
        activated_at=now.isoformat(),
    )
    logger.info("Activated %s plan for user=%s order=%s", plan, user_id, order_id)


# ── Query ──

def get_user_orders(user_id: str, limit: int = 50) -> list[dict]:
    """List payment history for a user."""
    return db.list_orders(user_id, limit=limit)


def get_order_status(order_id: str) -> dict | None:
    """Get full order details."""
    order = db.get_order(order_id)
    if not order:
        return None
    order.pop("metadata_json", None)
    return order
