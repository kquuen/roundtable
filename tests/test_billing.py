"""Tests for Phase 4 — billing, quota, export, and payment."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest

# Ensure JWT_SECRET is set for auth imports
os.environ.setdefault("JWT_SECRET", "test-secret-key-" + os.urandom(16).hex())

from roundtable import db
from roundtable.auth import get_user_store, User
from roundtable.billing import (
    PlanTier,
    get_plan_limits,
    consume_quota,
    get_user_usage_info,
    require_export_permission,
    _should_reset_quota,
    _maybe_reset_quota,
)
from roundtable.payment import (
    create_order,
    prepare_payment,
    handle_payment_callback,
    get_order_status,
    get_user_orders,
    OrderStatus,
)
from roundtable.export import generate_pdf_from_markdown, _strip_markdown


def _random_user():
    store = get_user_store()
    username = f"test_{uuid.uuid4().hex[:8]}"
    return store.create(username, f"{username}@test.com", "password123")


# ── Plan Limits ──

def test_plan_limits_free():
    limits = get_plan_limits("free")
    assert limits["monthly_quota"] == 3
    assert limits["allow_export"] is False
    assert limits["allow_custom_agents"] is False
    assert limits["price_cents_monthly"] == 0


def test_plan_limits_pro():
    limits = get_plan_limits("pro")
    assert limits["monthly_quota"] == 9999
    assert limits["allow_export"] is True
    assert limits["allow_custom_agents"] is True
    assert limits["price_cents_monthly"] == 2900


def test_plan_limits_team():
    limits = get_plan_limits("team")
    assert limits["allow_batch"] is True
    assert limits["price_cents_monthly"] == 9900


def test_plan_limits_unknown_fallback():
    limits = get_plan_limits("nonexistent")
    assert limits["monthly_quota"] == 3  # falls back to free


# ── Quota Consumption ──

def test_consume_quota_and_log():
    db.init_db()
    user = _random_user()
    # Reset to free plan with 3 quota
    db.update_user_plan(user.user_id, plan="free", monthly_quota=3)
    db.update_user_usage(user.user_id, 0)

    info = consume_quota(user.user_id, cost=1, action="test_action", session_id="s_1")
    assert info["monthly_used"] == 1
    assert info["remaining"] == 2

    # Log should exist
    logs = db.get_usage_logs(user.user_id)
    assert len(logs) >= 1
    assert logs[0]["action"] == "test_action"


def test_consume_quota_exceeds():
    db.init_db()
    user = _random_user()
    now = datetime.now(timezone.utc).isoformat()
    db.update_user_plan(user.user_id, plan="free", monthly_quota=2, quota_reset_at=now)
    db.update_user_usage(user.user_id, 2)

    with pytest.raises(ValueError):
        consume_quota(user.user_id, cost=1, action="test")


# ── Monthly Reset ──

def test_should_reset_quota_when_reset_at_missing():
    user = _random_user()
    assert _should_reset_quota(user) is True


def test_should_reset_quota_when_new_month():
    user = _random_user()
    # Mock a reset from last month
    last_month = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    db.update_user_plan(user.user_id, plan="free", quota_reset_at=last_month)
    # Refresh user
    store = get_user_store()
    refreshed = store.get_by_id(user.user_id)
    assert _should_reset_quota(refreshed) is True


def test_maybe_reset_quota_resets():
    user = _random_user()
    db.update_user_plan(user.user_id, plan="free", monthly_quota=3)
    db.update_user_usage(user.user_id, 2)
    # Force old reset date
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    db.update_user_plan(user.user_id, plan="free", quota_reset_at=old)

    store = get_user_store()
    refreshed = store.get_by_id(user.user_id)
    updated = _maybe_reset_quota(refreshed)
    assert updated.monthly_used == 0 or updated.monthly_quota == 3


# ── User Usage Info ──

def test_get_user_usage_info():
    db.init_db()
    user = _random_user()
    now = datetime.now(timezone.utc).isoformat()
    db.update_user_plan(user.user_id, plan="pro", monthly_quota=9999, quota_reset_at=now)
    db.update_user_usage(user.user_id, 5)

    info = get_user_usage_info(user.user_id)
    assert info["plan"] == "pro"
    assert info["monthly_used"] == 5
    assert info["remaining"] == 9994
    assert "limits" in info
    assert "monthly_summary" in info


# ── Export Permission ──

def test_require_export_permission_free_fails():
    user = _random_user()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        require_export_permission(user)
    assert exc.value.status_code == 403


def test_require_export_permission_pro_succeeds():
    user = _random_user()
    db.update_user_plan(user.user_id, plan="pro")
    store = get_user_store()
    refreshed = store.get_by_id(user.user_id)
    # Should not raise
    require_export_permission(refreshed)


# ── PDF Export ──

def test_strip_markdown():
    md = "**bold** and *italic* and `code`"
    plain = _strip_markdown(md)
    assert "**" not in plain
    assert "`" not in plain
    assert "bold" in plain


def test_generate_pdf_from_markdown():
    md = "# Title\n\n## Section\n\n- bullet one\n- bullet two\n\nSome paragraph."
    pdf_bytes = generate_pdf_from_markdown(md, title="Test")
    assert isinstance(pdf_bytes, (bytes, bytearray))
    assert len(pdf_bytes) > 100
    assert bytes(pdf_bytes[:4]) == b"%PDF"


def test_generate_pdf_with_cjk():
    md = "# 圆桌会议报告\n\n## 摘要\n\n- 第一条结论\n- 第二条结论"
    pdf_bytes = generate_pdf_from_markdown(md, title="报告")
    assert isinstance(pdf_bytes, (bytes, bytearray))
    assert len(pdf_bytes) > 100
    assert bytes(pdf_bytes[:4]) == b"%PDF"


# ── Payment ──

def test_create_order():
    db.init_db()
    user = _random_user()
    order = create_order(user.user_id, plan="pro", provider="wechat")
    assert order["order_id"].startswith("ord_")
    assert order["plan"] == "pro"
    assert order["amount_cents"] == 2900
    assert order["status"] == "pending"

    # Verify in DB
    db_order = get_order_status(order["order_id"])
    assert db_order is not None
    assert db_order["user_id"] == user.user_id


def test_create_order_invalid_plan():
    user = _random_user()
    with pytest.raises(ValueError):
        create_order(user.user_id, plan="free")


def test_prepare_payment():
    db.init_db()
    user = _random_user()
    order = create_order(user.user_id, plan="pro", provider="wechat")
    prep = prepare_payment(order["order_id"])
    assert prep["order_id"] == order["order_id"]
    assert prep["provider"] == "wechat"
    assert "payload" in prep


def test_handle_wechat_callback():
    db.init_db()
    user = _random_user()
    order = create_order(user.user_id, plan="pro", provider="wechat")

    result = handle_payment_callback(
        provider="wechat",
        payload={
            "out_trade_no": order["order_id"],
            "transaction_id": "wx_txn_123",
            "result_code": "SUCCESS",
        },
        signature="",
    )
    assert result["status"] == "activated"
    assert result["plan"] == "pro"

    # User should now be pro
    store = get_user_store()
    updated = store.get_by_id(user.user_id)
    assert updated.plan == "pro"


def test_handle_alipay_callback():
    db.init_db()
    user = _random_user()
    order = create_order(user.user_id, plan="team", provider="alipay")

    result = handle_payment_callback(
        provider="alipay",
        payload={
            "out_trade_no": order["order_id"],
            "trade_no": "ali_txn_456",
            "trade_status": "TRADE_SUCCESS",
        },
        signature="",
    )
    assert result["status"] == "activated"
    assert result["plan"] == "team"


def test_callback_idempotent():
    db.init_db()
    user = _random_user()
    order = create_order(user.user_id, plan="pro", provider="wechat")

    payload = {
        "out_trade_no": order["order_id"],
        "transaction_id": "wx_txn_789",
        "result_code": "SUCCESS",
    }
    r1 = handle_payment_callback("wechat", payload, "")
    r2 = handle_payment_callback("wechat", payload, "")
    assert r1["status"] == "activated"
    assert r2.get("message") == "already processed"


def test_list_orders():
    db.init_db()
    user = _random_user()
    o1 = create_order(user.user_id, plan="pro")
    o2 = create_order(user.user_id, plan="team")
    orders = get_user_orders(user.user_id)
    assert len(orders) >= 2
    order_ids = {o["order_id"] for o in orders}
    assert o1["order_id"] in order_ids
    assert o2["order_id"] in order_ids
