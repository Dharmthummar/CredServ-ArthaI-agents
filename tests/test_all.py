"""
Test suite for Phase 1 (Verification Engine) and Phase 2 (State Machine).
Run with:  python -m pytest tests/ -v
"""

import json
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "phase1"))
sys.path.insert(0, str(ROOT / "phase2"))

from extractor import (
    BankStatement,
    Transaction,
    dict_to_statement,
    parse_json_response,
    verify_statement,
    _to_decimal,
)
from collections_orchestrator import (
    BorrowerProfile,
    BorrowerState,
    CollectionState,
    node_d15_pending,
    node_d7_reminder,
    node_d1_final_reminder,
    node_due_date_check,
    node_delinquent_d1,
    node_delinquent_d3,
    node_closed_paid,
    node_closed_disputed,
    export_audit_log,
    run_pipeline_python,
)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 TESTS – Verification Engine
# ═══════════════════════════════════════════════════════════════════════════

class TestToDecimal:
    def test_valid_string(self):
        assert _to_decimal("12345.67") == Decimal("12345.67")

    def test_with_commas(self):
        assert _to_decimal("1,23,456.78") == Decimal("123456.78")

    def test_none_returns_zero(self):
        assert _to_decimal(None) == Decimal("0")

    def test_empty_string_returns_zero(self):
        assert _to_decimal("") == Decimal("0")

    def test_null_string_returns_zero(self):
        assert _to_decimal("null") == Decimal("0")


class TestVerifyStatement:
    """Tests for the deterministic balance verification guardrail."""

    def _make_statement(self, rows) -> BankStatement:
        txns = [
            Transaction(date=r[0], description=r[1], debit=r[2], credit=r[3], balance=r[4])
            for r in rows
        ]
        return BankStatement(
            account_holder_name="Test User",
            bank_name="Test Bank",
            account_number="123456789",
            transactions=txns,
        )

    def test_clean_statement_passes(self):
        rows = [
            ("01-06-2024", "Opening",   None,       None,      "25000.00"),
            ("02-06-2024", "NEFT In",   None,    "15000.00", "40000.00"),
            ("05-06-2024", "Amazon",  "2499.00",   None,      "37501.00"),
            ("12-06-2024", "Salary",    None,    "75000.00", "112501.00"),
            ("15-06-2024", "Rent",    "18000.00",  None,      "94501.00"),
        ]
        stmt = self._make_statement(rows)
        passed, notes = verify_statement(stmt)
        assert passed, f"Should pass but failed: {notes}"

    def test_wrong_balance_detected(self):
        rows = [
            ("01-06-2024", "Opening",  None,      None,     "25000.00"),
            ("02-06-2024", "NEFT In",  None,   "15000.00", "39000.00"),  # wrong! should be 40000
        ]
        stmt = self._make_statement(rows)
        passed, notes = verify_statement(stmt)
        assert not passed
        assert "mismatch" in notes.lower()

    def test_empty_transactions_fails(self):
        stmt = BankStatement("A", "B", "123", transactions=[])
        passed, notes = verify_statement(stmt)
        assert not passed
        assert "no transactions" in notes.lower()

    def test_single_transaction_passes(self):
        rows = [("01-01-2024", "Opening", None, None, "5000.00")]
        stmt = self._make_statement(rows)
        passed, notes = verify_statement(stmt)
        assert passed

    def test_debit_credit_swap_detected(self):
        """If debit and credit are swapped, the running balance will mismatch."""
        rows = [
            ("01-06-2024", "Opening",  None,      None,     "10000.00"),
            # correct: debit 5000 → balance 5000; but let's swap them
            ("02-06-2024", "Payment",  None,   "5000.00",   "5000.00"),  # credit instead of debit
        ]
        stmt = self._make_statement(rows)
        passed, notes = verify_statement(stmt)
        assert not passed

    def test_rounding_tolerance(self):
        """1-paisa rounding differences should be tolerated."""
        rows = [
            ("01-06-2024", "Opening",  None,    None,       "10000.00"),
            ("02-06-2024", "Interest", None,  "333.33",     "10333.33"),
            # Rounding: 10333.33 - 100 = 10233.33 printed as 10233.34 (1 paisa off)
            ("03-06-2024", "Fee",    "100.00",  None,       "10233.34"),
        ]
        stmt = self._make_statement(rows)
        passed, notes = verify_statement(stmt)
        # 1-paisa diff should pass (within 0.02 tolerance)
        assert passed


class TestParseJsonResponse:
    def test_clean_json(self):
        raw = '{"account_holder_name": "Test", "bank_name": "SBI", "account_number": "123", "transactions": []}'
        result = parse_json_response(raw)
        assert result["account_holder_name"] == "Test"

    def test_strips_markdown_fences(self):
        raw = '```json\n{"account_holder_name": "Test", "bank_name": "SBI", "account_number": "123", "transactions": []}\n```'
        result = parse_json_response(raw)
        assert result["bank_name"] == "SBI"

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError):
            parse_json_response("I could not extract the data.")

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_json_response("{ bad json }")


class TestDictToStatement:
    def test_full_conversion(self):
        data = {
            "account_holder_name": "Priya Sharma",
            "bank_name": "HDFC Bank",
            "account_number": "1234567890",
            "transactions": [
                {"date": "01-06-2024", "description": "Opening", "debit": None, "credit": None, "balance": "10000.00"},
                {"date": "05-06-2024", "description": "ATM", "debit": "2000.00", "credit": None, "balance": "8000.00"},
            ],
        }
        stmt = dict_to_statement(data)
        assert stmt.account_holder_name == "Priya Sharma"
        assert len(stmt.transactions) == 2
        assert stmt.transactions[1].debit == "2000.00"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 TESTS – State Machine
# ═══════════════════════════════════════════════════════════════════════════

def make_borrower_state() -> BorrowerState:
    borrower = BorrowerProfile(
        borrower_id="BRW-TEST-001",
        name="Test Borrower",
        phone="+91-9999999999",
        email="test@example.com",
        loan_id="LN-TEST-001",
        outstanding_amount=10000.00,
        due_date=date(2024, 6, 30),
    )
    return BorrowerState(borrower=borrower)


class TestStateMachineNodes:

    def test_d15_transitions_to_d7(self):
        state = make_borrower_state()
        result = node_d15_pending(state)
        assert result.current_state == CollectionState.REMINDER_SENT_D7

    def test_d7_transitions_to_d1(self):
        state = make_borrower_state()
        state.current_state = CollectionState.REMINDER_SENT_D7
        with patch("collections_orchestrator.mock_send_sms"), \
             patch("collections_orchestrator.mock_send_email"):
            result = node_d7_reminder(state)
        assert result.current_state == CollectionState.FINAL_REMINDER_D1
        assert len(result.contact_history) == 2  # SMS + email

    def test_d1_push_then_due_date_check(self):
        state = make_borrower_state()
        state.current_state = CollectionState.FINAL_REMINDER_D1
        with patch("collections_orchestrator.mock_send_push"):
            result = node_d1_final_reminder(state)
        assert result.current_state == CollectionState.DUE_DATE_CHECK

    def test_due_date_check_paid_closes(self):
        state = make_borrower_state()
        state.current_state = CollectionState.DUE_DATE_CHECK
        with patch("collections_orchestrator.mock_check_payment_status", return_value=True):
            result = node_due_date_check(state)
        assert result.current_state == CollectionState.CLOSED_PAID
        assert result.is_paid is True

    def test_due_date_check_unpaid_escalates(self):
        state = make_borrower_state()
        state.current_state = CollectionState.DUE_DATE_CHECK
        with patch("collections_orchestrator.mock_check_payment_status", return_value=False):
            result = node_due_date_check(state)
        assert result.current_state == CollectionState.DELINQUENT_D1

    def test_d1_overdue_unpaid_goes_to_d3(self):
        state = make_borrower_state()
        state.current_state = CollectionState.DELINQUENT_D1
        with patch("collections_orchestrator.mock_send_sms"), \
             patch("collections_orchestrator.mock_check_payment_status", return_value=False):
            result = node_delinquent_d1(state)
        assert result.current_state == CollectionState.DELINQUENT_D3

    def test_d3_dispute_closes_as_disputed(self):
        state = make_borrower_state()
        state.current_state = CollectionState.DELINQUENT_D3
        dispute_result = {
            "status": "completed",
            "outcome": "dispute",
            "transcript": "Borrower: I never took this loan.",
            "dispute_reason": "Borrower denies the loan.",
        }
        with patch("collections_orchestrator.mock_trigger_voice_call", return_value=dispute_result), \
             patch("collections_orchestrator.mock_assign_human_agent", return_value={"agent_id": "AGT-001"}):
            result = node_delinquent_d3(state)
        assert result.current_state == CollectionState.CLOSED_DISPUTED
        assert result.is_disputed is True
        assert result.dispute_reason == "Borrower denies the loan."

    def test_d3_payment_promised_escalates_if_not_confirmed(self):
        state = make_borrower_state()
        state.current_state = CollectionState.DELINQUENT_D3
        promise_result = {
            "status": "completed",
            "outcome": "payment_promised",
            "transcript": "Borrower: I'll pay tomorrow.",
        }
        with patch("collections_orchestrator.mock_trigger_voice_call", return_value=promise_result), \
             patch("collections_orchestrator.mock_check_payment_status", return_value=False):
            result = node_delinquent_d3(state)
        assert result.current_state == CollectionState.ESCALATED_HUMAN

    def test_full_pipeline_dispute_path(self):
        """End-to-end test: all states leading to CLOSED_DISPUTED."""
        state = make_borrower_state()
        dispute_result = {
            "status": "completed",
            "outcome": "dispute",
            "transcript": "Borrower: This is not my loan.",
            "dispute_reason": "Identity mismatch claimed.",
        }
        with patch("collections_orchestrator.mock_send_sms"), \
             patch("collections_orchestrator.mock_send_email"), \
             patch("collections_orchestrator.mock_send_push"), \
             patch("collections_orchestrator.mock_check_payment_status", return_value=False), \
             patch("collections_orchestrator.mock_trigger_voice_call", return_value=dispute_result), \
             patch("collections_orchestrator.mock_assign_human_agent", return_value={"agent_id": "AGT-XYZ"}):
            final = run_pipeline_python(state)
        assert final.current_state == CollectionState.CLOSED_DISPUTED
        assert final.is_disputed is True
        assert len(final.state_transitions if hasattr(final, 'state_transitions') else final.notes) > 0

    def test_full_pipeline_paid_path(self):
        """End-to-end test: payment received on due date."""
        state = make_borrower_state()
        with patch("collections_orchestrator.mock_send_sms"), \
             patch("collections_orchestrator.mock_send_email"), \
             patch("collections_orchestrator.mock_send_push"), \
             patch("collections_orchestrator.mock_check_payment_status", return_value=True):
            final = run_pipeline_python(state)
        assert final.current_state == CollectionState.CLOSED_PAID
        assert final.is_paid is True


class TestAuditLog:
    def test_audit_log_structure(self):
        state = make_borrower_state()
        state.current_state = CollectionState.CLOSED_PAID
        state.is_paid = True
        log = export_audit_log(state)

        required_keys = {
            "run_id", "loan_id", "borrower_id", "final_state",
            "is_paid", "is_disputed", "contacts", "state_transitions",
        }
        assert required_keys.issubset(log.keys())
        assert log["final_state"] == "CLOSED_PAID"
        assert log["is_paid"] is True

    def test_audit_log_contacts_are_serialisable(self):
        state = make_borrower_state()
        state.log_contact(
            __import__("collections_orchestrator").ContactChannel.SMS,
            "Test message",
            "sent",
        )
        log = export_audit_log(state)
        assert len(log["contacts"]) == 1
        c = log["contacts"][0]
        assert "timestamp" in c
        assert "channel" in c
        assert c["outcome"] == "sent"
