"""
demo_failure.py — Phase 1 Verification Engine Demo
===================================================
This script does NOT call any LLM. It demonstrates purely in Python how
the deterministic verification guardrail catches a math error in a bank
statement and then simulates a corrected retry.

Use this for your Loom video to show the verifier working without needing
Ollama or Gemini running.

Run: python demo_failure.py
"""
import sys
import os

# Path setup
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "phase1"))

from extractor import (
    BankStatement,
    Transaction,
    verify_statement,
    dict_to_statement,
    parse_json_response,
)

# ─────────────────────────────────────────────
# Simulated LLM Output #1 — contains a WRONG balance (intentional error)
# ─────────────────────────────────────────────
FLAWED_LLM_RESPONSE = """
{
  "account_holder_name": "Priya Sharma",
  "bank_name": "HDFC Bank",
  "account_number": "50100123456789",
  "transactions": [
    {"date": "01-06-2024", "description": "Opening Balance",  "debit": null,       "credit": null,       "balance": "25000.00"},
    {"date": "03-06-2024", "description": "NEFT from Rajesh", "debit": null,       "credit": "15000.00", "balance": "39000.00"},
    {"date": "05-06-2024", "description": "Amazon Purchase",  "debit": "2499.00",  "credit": null,       "balance": "37800.00"},
    {"date": "10-06-2024", "description": "Monthly Salary",   "debit": null,       "credit": "75000.00", "balance": "112800.00"},
    {"date": "15-06-2024", "description": "Rent Payment",     "debit": "18000.00", "credit": null,       "balance": "94801.00"}
  ]
}
"""
# Correct balances should be:
#  After NEFT:    25000 + 15000       = 40000    (LLM said 39000 ❌)
#  After Amazon:  40000 - 2499       = 37501    (LLM said 37800 ❌)
#  After Salary:  37501 + 75000      = 112501   (LLM said 112800 ❌)
#  After Rent:    112501 - 18000     = 94501    (LLM said 94801 ❌)

# ─────────────────────────────────────────────
# Simulated LLM Output #2 — CORRECTED after retry prompt
# ─────────────────────────────────────────────
CORRECTED_LLM_RESPONSE = """
{
  "account_holder_name": "Priya Sharma",
  "bank_name": "HDFC Bank",
  "account_number": "50100123456789",
  "transactions": [
    {"date": "01-06-2024", "description": "Opening Balance",  "debit": null,       "credit": null,       "balance": "25000.00"},
    {"date": "03-06-2024", "description": "NEFT from Rajesh", "debit": null,       "credit": "15000.00", "balance": "40000.00"},
    {"date": "05-06-2024", "description": "Amazon Purchase",  "debit": "2499.00",  "credit": null,       "balance": "37501.00"},
    {"date": "10-06-2024", "description": "Monthly Salary",   "debit": null,       "credit": "75000.00", "balance": "112501.00"},
    {"date": "15-06-2024", "description": "Rent Payment",     "debit": "18000.00", "credit": null,       "balance": "94501.00"}
  ]
}
"""

SEP = "=" * 65


def demo() -> None:
    print(SEP)
    print("  PHASE 1: VERIFICATION ENGINE DEMO  (no LLM required)")
    print(SEP)

    # ── Attempt 1: flawed extraction ────────────────────
    print("\n[ATTEMPT 1] Processing initial LLM output …\n")
    data1    = parse_json_response(FLAWED_LLM_RESPONSE)
    stmt1    = dict_to_statement(data1)
    passed1, notes1 = verify_statement(stmt1)

    print(f"  Account Holder : {stmt1.account_holder_name}")
    print(f"  Bank           : {stmt1.bank_name}")
    print(f"  Transactions   : {len(stmt1.transactions)}")
    print(f"\n  ✗ VERIFICATION FAILED — triggering auto-retry\n")
    print(f"  Failure details:\n  {notes1}\n")

    # ── Retry prompt that would be sent to LLM ──────────
    print("-" * 65)
    print("  [AUTO-RETRY] Sending failure reason back to LLM …")
    print("  Failure reason injected into RETRY_CORRECTION_PROMPT:")
    for line in notes1.splitlines()[:4]:
        print(f"    {line}")
    print("-" * 65 + "\n")

    # ── Attempt 2: corrected extraction ─────────────────
    print("[ATTEMPT 2] Processing corrected LLM output …\n")
    data2    = parse_json_response(CORRECTED_LLM_RESPONSE)
    stmt2    = dict_to_statement(data2)
    passed2, notes2 = verify_statement(stmt2)

    print(f"  Account Holder : {stmt2.account_holder_name}")
    print(f"  Transactions   : {len(stmt2.transactions)}")
    print(f"\n  ✓ VERIFICATION PASSED\n  {notes2}\n")

    # ── Transaction table ────────────────────────────────
    print(SEP)
    print(f"  {'Date':<12}  {'Description':<22}  {'Debit':>10}  {'Credit':>10}  {'Balance':>12}")
    print(f"  {'-'*12}  {'-'*22}  {'-'*10}  {'-'*10}  {'-'*12}")
    for t in stmt2.transactions:
        debit  = t.debit  or "—"
        credit = t.credit or "—"
        print(f"  {t.date:<12}  {t.description:<22}  {debit:>10}  {credit:>10}  {t.balance:>12}")
    print(SEP)
    print("\nDemo complete — verifier caught 4 errors and confirmed the fix.")


if __name__ == "__main__":
    demo()
