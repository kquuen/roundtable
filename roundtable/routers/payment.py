"""Payment endpoints — order creation, callback, and status queries."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from roundtable.auth import User, require_user, require_admin
from roundtable.payment import (
    create_order,
    prepare_payment,
    handle_payment_callback,
    get_user_orders,
    get_order_status,
    OrderStatus,
)

logger = logging.getLogger("roundtable.routers.payment")
router = APIRouter(prefix="/payment", tags=["payment"])


# ── Request/Response Models ──

class CreateOrderRequest(BaseModel):
    plan: str = Field(..., pattern="^(pro|team)$")
    provider: str = Field(default="wechat", pattern="^(wechat|alipay|stripe)$")


class PaymentCallbackRequest(BaseModel):
    provider: str = Field(..., pattern="^(wechat|alipay|stripe)$")
    payload: dict = Field(default_factory=dict)
    signature: str = ""


# ── Endpoints ──

@router.post("/create-order")
async def payment_create_order(req: CreateOrderRequest, user: User = Depends(require_user)):
    """Create a payment order for plan upgrade."""
    try:
        order = create_order(
            user_id=user.user_id,
            plan=req.plan,
            provider=req.provider,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return order


@router.get("/order/{order_id}/prepare")
async def payment_prepare(order_id: str, user: User = Depends(require_user)):
    """Get payment params (prepay_id / QR code) for the frontend."""
    order = get_order_status(order_id)
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    if order["user_id"] != user.user_id:
        raise HTTPException(403, "Not your order")
    try:
        result = prepare_payment(order_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/callback")
async def payment_callback(req: PaymentCallbackRequest):
    """Webhook endpoint for payment provider callbacks.

    In production this should be protected by provider signature verification.
    """
    try:
        result = handle_payment_callback(
            provider=req.provider,
            payload=req.payload,
            signature=req.signature,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.get("/orders")
async def payment_list_orders(user: User = Depends(require_user)):
    """List current user's payment orders."""
    return {"orders": get_user_orders(user.user_id)}


@router.get("/order/{order_id}")
async def payment_get_order(order_id: str, user: User = Depends(require_user)):
    """Get order details."""
    order = get_order_status(order_id)
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    if order["user_id"] != user.user_id:
        # Admin can view any order
        from roundtable.settings import get_settings
        if user.username not in get_settings().admin_user_list:
            raise HTTPException(403, "Not your order")
    return order


# ── Admin ──

@router.post("/order/{order_id}/activate")
async def admin_activate_order(order_id: str, admin: User = Depends(require_admin)):
    """Manually activate an order (admin only, for support)."""
    from roundtable.payment import _activate_subscription

    order = get_order_status(order_id)
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    if order["status"] == OrderStatus.ACTIVATED.value:
        return {"order_id": order_id, "status": "already_activated"}

    _activate_subscription(order_id, order["user_id"], order["plan"])
    return {
        "order_id": order_id,
        "status": OrderStatus.ACTIVATED.value,
        "activated_by": admin.username,
    }
