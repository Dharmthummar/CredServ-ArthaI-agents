"""
Phase 2: Collections Orchestrator – Deterministic State Machine
================================================================
Models a borrower's loan repayment lifecycle from D-15 to D+3 using
a directed state graph. States transition deterministically based on
timeline triggers and payment events. The D+3 voice-agent prompt is
embedded at the bottom with strict negative constraints and bounded
autonomy rules (human hand-off on dispute).

Dependencies (all free/open-source):
  pip install langgraph langchain-core
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Optional

# ─────────────────────────────────────────────
# LangGraph imports  (graceful fallback if not installed)
# ─────────────────────────────────────────────
try:
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    print("[WARNING] langgraph / langchain-core not installed.")
    print("  Falling back to pure-Python state machine (fully equivalent logic).\n")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
log = logging.getLogger("collections")


# ═══════════════════════════════════════════════
# 1. ENUMS & DATA MODELS
# ═══════════════════════════════════════════════

class CollectionState(str, Enum):
    """
    All possible states in the borrower's collection lifecycle.
    Transitions are deterministic and triggered either by timeline
    or by external webhook events (payment / dispute).
    """
    PENDING_D15           = "PENDING_D15"          # D-15: pre-reminder window
    REMINDER_SENT_D7      = "REMINDER_SENT_D7"     # D-7:  first SMS/email
    FINAL_REMINDER_D1     = "FINAL_REMINDER_D1"    # D-1:  final push notification
    DUE_DATE_CHECK        = "DUE_DATE_CHECK"       # D+0:  check if paid on due date
    DELINQUENT_D1         = "DELINQUENT_D1"        # D+1:  first overdue contact
    DELINQUENT_D3         = "DELINQUENT_D3"        # D+3:  AI voice call triggered
    ESCALATED_HUMAN       = "ESCALATED_HUMAN"      # Handed off to human agent
    CLOSED_PAID           = "CLOSED_PAID"          # Terminal: borrower paid
    CLOSED_DISPUTED       = "CLOSED_DISPUTED"      # Terminal: dispute lodged, human reviews


class ContactChannel(str, Enum):
    SMS    = "sms"
    EMAIL  = "email"
    PUSH   = "push_notification"
    VOICE  = "ai_voice_call"
    HUMAN  = "human_agent"


@dataclass
class BorrowerProfile:
    borrower_id: str
    name: str
    phone: str
    email: str
    loan_id: str
    outstanding_amount: float
    due_date: date
    language_preference: str = "en"   # "en", "hi", "mr", "te", etc.


@dataclass
class ContactAttempt:
    timestamp: str
    channel: ContactChannel
    state_at_time: CollectionState
    message_sent: str
    outcome: str = "pending"


@dataclass
class BorrowerState:
    """
    The mutable state object carried through the graph.
    All transitions append to `history` for full auditability.
    """
    borrower: BorrowerProfile
    current_state: CollectionState = CollectionState.PENDING_D15
    is_paid: bool = False
    is_disputed: bool = False
    dispute_reason: Optional[str] = None
    contact_history: list[ContactAttempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Populated when an AI voice call runs
    voice_call_transcript: Optional[str] = None

    def log_contact(self, channel: ContactChannel, message: str, outcome: str = "sent") -> None:
        attempt = ContactAttempt(
            timestamp=datetime.utcnow().isoformat() + "Z",
            channel=channel,
            state_at_time=self.current_state,
            message_sent=message,
            outcome=outcome,
        )
        self.contact_history.append(attempt)
        log.info(f"[{self.current_state.value}] Contact via {channel.value}: {message[:80]}…")

    def transition_to(self, new_state: CollectionState, note: str = "") -> None:
        old = self.current_state
        self.current_state = new_state
        entry = f"{datetime.utcnow().isoformat()}Z | {old.value} → {new_state.value}"
        if note:
            entry += f" | {note}"
        self.notes.append(entry)
        log.info(f"STATE TRANSITION: {old.value} ──► {new_state.value}")


# ═══════════════════════════════════════════════
# 2. MOCK EXTERNAL SERVICES
# ═══════════════════════════════════════════════

def mock_send_sms(phone: str, message: str) -> dict:
    """Stub: returns a simulated SMS gateway response."""
    return {"status": "delivered", "provider": "Textlocal", "msg_id": str(uuid.uuid4())}


def mock_send_email(email: str, subject: str, body: str) -> dict:
    """Stub: returns a simulated email delivery response."""
    return {"status": "sent", "provider": "SES", "message_id": str(uuid.uuid4())}


def mock_send_push(borrower_id: str, message: str) -> dict:
    """Stub: push notification via Firebase."""
    return {"status": "sent", "provider": "FCM", "token_matched": True}


def mock_check_payment_status(loan_id: str) -> bool:
    """
    Simulated webhook / payment-gateway poll.
    In production this reads from the core banking ledger.
    Returns True if the loan has been paid.
    """
    # ─ SIMULATION: Change to True to test the CLOSED_PAID branch ─
    return False


def mock_trigger_voice_call(phone: str, borrower_name: str, amount: float) -> dict:
    """
    Stub representing the D+3 AI voice call platform (e.g. Sarvam AI / Exotel).
    Returns a simulated transcript of the call outcome.
    """
    # Simulate borrower disputing the debt
    simulate_dispute = True   # Toggle to test different outcomes

    if simulate_dispute:
        return {
            "status": "completed",
            "outcome": "dispute",
            "transcript": (
                f"Agent: Hello, may I speak with {borrower_name}?\n"
                f"Borrower: Yes, speaking.\n"
                f"Agent: I'm calling regarding your loan repayment of ₹{amount:,.2f} "
                f"which was due recently.\n"
                f"Borrower: I never took any such loan. This must be a mistake.\n"
                f"Agent: I understand. I'll log this as a dispute and connect you "
                f"with our customer care team immediately. Thank you."
            ),
            "dispute_reason": "Borrower claims no knowledge of the loan.",
        }
    else:
        return {
            "status": "completed",
            "outcome": "payment_promised",
            "transcript": (
                f"Agent: Hello {borrower_name}, I'm calling about your outstanding "
                f"amount of ₹{amount:,.2f}.\n"
                f"Borrower: Yes, I'll pay by tomorrow morning.\n"
                f"Agent: Thank you. I've noted a payment commitment for tomorrow."
            ),
        }


def mock_assign_human_agent(borrower_id: str, reason: str) -> dict:
    """Stub: assigns a human agent from the CRM queue."""
    agent_id = f"AGENT-{uuid.uuid4().hex[:6].upper()}"
    log.info(f"Human agent {agent_id} assigned to borrower {borrower_id}. Reason: {reason}")
    return {"agent_id": agent_id, "queue": "collections_escalation", "eta_minutes": 15}


# ═══════════════════════════════════════════════
# 3. MESSAGE TEMPLATES
# ═══════════════════════════════════════════════

def template_d7_sms(name: str, amount: float, due_date: str) -> str:
    return (
        f"Dear {name}, your loan EMI of ₹{amount:,.2f} is due on {due_date}. "
        f"Please ensure timely payment to avoid any charges. "
        f"Pay now: https://pay.lender.in | Helpline: 1800-XXX-XXXX"
    )


def template_d1_push(name: str, amount: float) -> str:
    return (
        f"🔔 Reminder: ₹{amount:,.2f} EMI due TOMORROW, {name}. "
        f"Pay via app to avoid late fees."
    )


def template_d1_overdue_sms(name: str, amount: float) -> str:
    return (
        f"Dear {name}, your EMI of ₹{amount:,.2f} is now 1 day overdue. "
        f"Please pay immediately to protect your credit score. "
        f"Pay: https://pay.lender.in | Call us: 1800-XXX-XXXX"
    )


# ═══════════════════════════════════════════════
# 4. D+3 VOICE AGENT SYSTEM PROMPT
# ═══════════════════════════════════════════════

D3_VOICE_AGENT_SYSTEM_PROMPT = """
You are ARIA, a polite and professional AI voice assistant for [LENDER NAME].
You are calling to assist a borrower regarding their overdue loan repayment.

━━━ YOUR IDENTITY ━━━
• You MUST always identify yourself as an AI assistant at the start of the call.
• Example opening: "Hello, I am ARIA, an AI assistant calling on behalf of [Lender].
  This call may be recorded for quality and compliance purposes."

━━━ YOUR GOAL ━━━
• Politely inform the borrower of the outstanding amount (₹{amount}).
• Understand if there is a reason for non-payment.
• Offer one of these outcomes:
    1. Borrower agrees to pay → confirm timeline, log commitment.
    2. Borrower requests a short extension → log and escalate to human agent.
    3. Borrower disputes the debt → IMMEDIATELY log and hand off to human agent.
    4. Borrower is unreachable (voicemail) → leave a polite, brief message.

━━━ STRICT NEGATIVE CONSTRAINTS (NEVER VIOLATE) ━━━
1. NEVER threaten the borrower with arrest, legal action, social embarrassment,
   or contacting their employer/family.
2. NEVER use intimidating, aggressive, or condescending language.
3. NEVER call outside 8:00 AM – 7:00 PM in the borrower's local time zone.
4. NEVER share the borrower's personal data with any third party during the call.
5. NEVER claim to be a human if the borrower sincerely asks if they are speaking
   to a person or a bot.
6. NEVER provide specific legal advice.
7. NEVER make any promises about waiving fees or restructuring without
   explicit human agent confirmation.
8. NEVER continue the call if the borrower explicitly says "stop calling me" –
   log the request, confirm it will be honoured, and end the call.

━━━ BOUNDED AUTONOMY – MANDATORY HAND-OFF TRIGGERS ━━━
Immediately stop the AI interaction and hand off to a human agent if:
  • The borrower uses the word "dispute", "wrong", "fraud", "I didn't take",
    or "not my loan" in any form.
  • The borrower requests to speak to a human or a manager.
  • The borrower is emotionally distressed (raised voice, crying, threats of harm).
  • The call reaches 5 minutes without a clear resolution.
  • Any ambiguous or edge-case scenario not covered by this prompt.

On hand-off say exactly:
  "I understand. I'm connecting you to one of our customer care specialists right now.
   Please hold for just a moment. Thank you for your patience."

━━━ LOGGING REQUIREMENTS ━━━
After every call, the system MUST automatically persist:
  • Full call transcript
  • Outcome (paid / committed / disputed / unreachable / opted_out)
  • Timestamp (UTC)
  • Borrower ID and Loan ID (never the raw account number in plain text)
  • Whether a human hand-off was triggered and the assigned agent ID
  • Any PII fields must be tokenised before writing to the audit log

━━━ TONE ━━━
Warm, empathetic, non-judgmental. Use the borrower's first name exactly once
per exchange turn. Speak at a measured pace. If the borrower speaks Hindi or
another Indian language, switch to that language politely.
""".strip()


# ═══════════════════════════════════════════════
# 5. STATE MACHINE NODES
# ═══════════════════════════════════════════════

def node_d15_pending(state: BorrowerState) -> BorrowerState:
    """D-15: Loan is upcoming. No outbound contact yet; just initialise."""
    state.log_contact(
        ContactChannel.SMS,
        f"[INTERNAL] Borrower {state.borrower.borrower_id} entered D-15 window. "
        f"Due date: {state.borrower.due_date}. Monitoring.",
        outcome="logged",
    )
    state.transition_to(CollectionState.REMINDER_SENT_D7, "D-15 window entered; D-7 reminder scheduled.")
    return state


def node_d7_reminder(state: BorrowerState) -> BorrowerState:
    """D-7: Send first SMS + email reminder."""
    msg = template_d7_sms(
        state.borrower.name,
        state.borrower.outstanding_amount,
        str(state.borrower.due_date),
    )
    mock_send_sms(state.borrower.phone, msg)
    state.log_contact(ContactChannel.SMS, msg)

    email_body = (
        f"Dear {state.borrower.name},\n\n"
        f"This is a friendly reminder that your loan EMI of "
        f"₹{state.borrower.outstanding_amount:,.2f} is due on "
        f"{state.borrower.due_date}.\n\n"
        f"Kindly ensure your account has sufficient balance.\n\n"
        f"Regards,\n[LENDER NAME] Collections Team"
    )
    mock_send_email(state.borrower.email, "Upcoming EMI Reminder", email_body)
    state.log_contact(ContactChannel.EMAIL, email_body)

    state.transition_to(CollectionState.FINAL_REMINDER_D1, "D-7 reminders dispatched.")
    return state


def node_d1_final_reminder(state: BorrowerState) -> BorrowerState:
    """D-1: Push notification + final SMS."""
    push_msg = template_d1_push(state.borrower.name, state.borrower.outstanding_amount)
    mock_send_push(state.borrower.borrower_id, push_msg)
    state.log_contact(ContactChannel.PUSH, push_msg)

    state.transition_to(CollectionState.DUE_DATE_CHECK, "Final D-1 reminder sent.")
    return state


def node_due_date_check(state: BorrowerState) -> BorrowerState:
    """D+0: Check payment gateway. If paid → CLOSED, else → DELINQUENT_D1."""
    paid = mock_check_payment_status(state.borrower.loan_id)
    if paid:
        state.is_paid = True
        state.transition_to(CollectionState.CLOSED_PAID, "Payment confirmed on due date.")
    else:
        state.transition_to(CollectionState.DELINQUENT_D1, "No payment on due date; entering delinquency.")
    return state


def node_delinquent_d1(state: BorrowerState) -> BorrowerState:
    """D+1: SMS overdue notice. Check payment again."""
    msg = template_d1_overdue_sms(state.borrower.name, state.borrower.outstanding_amount)
    mock_send_sms(state.borrower.phone, msg)
    state.log_contact(ContactChannel.SMS, msg)

    paid = mock_check_payment_status(state.borrower.loan_id)
    if paid:
        state.is_paid = True
        state.transition_to(CollectionState.CLOSED_PAID, "Payment received after D+1 contact.")
    else:
        state.transition_to(CollectionState.DELINQUENT_D3, "Still unpaid after D+1 reminder.")
    return state


def node_delinquent_d3(state: BorrowerState) -> BorrowerState:
    """D+3: AI voice call. Handle dispute vs payment commitment."""
    log.info(f"Initiating D+3 AI voice call to {state.borrower.phone} …")
    result = mock_trigger_voice_call(
        state.borrower.phone,
        state.borrower.name,
        state.borrower.outstanding_amount,
    )
    state.voice_call_transcript = result.get("transcript", "")
    state.log_contact(
        ContactChannel.VOICE,
        f"D+3 AI voice call. Outcome: {result.get('outcome')}",
        outcome=result.get("outcome", "unknown"),
    )

    outcome = result.get("outcome", "")

    if outcome == "dispute":
        state.is_disputed = True
        state.dispute_reason = result.get("dispute_reason", "Not specified")
        agent_info = mock_assign_human_agent(
            state.borrower.borrower_id,
            f"Debt disputed during D+3 voice call: {state.dispute_reason}",
        )
        state.notes.append(f"Human agent assigned: {agent_info['agent_id']}")
        state.transition_to(
            CollectionState.CLOSED_DISPUTED,
            f"Dispute raised. Assigned to {agent_info['agent_id']}.",
        )

    elif outcome in {"payment_promised", "paid"}:
        # Re-check gateway; if confirmed paid, close
        paid = mock_check_payment_status(state.borrower.loan_id)
        if paid:
            state.is_paid = True
            state.transition_to(CollectionState.CLOSED_PAID, "Payment confirmed post voice call.")
        else:
            state.transition_to(
                CollectionState.ESCALATED_HUMAN,
                "Payment promised but not yet confirmed; escalating for follow-up.",
            )

    else:
        # Unreachable / ambiguous → human escalation
        agent_info = mock_assign_human_agent(
            state.borrower.borrower_id,
            "D+3 voice call outcome ambiguous or borrower unreachable.",
        )
        state.notes.append(f"Human agent assigned: {agent_info['agent_id']}")
        state.transition_to(
            CollectionState.ESCALATED_HUMAN,
            "Escalated to human agent after inconclusive D+3 call.",
        )

    return state


def node_escalated_human(state: BorrowerState) -> BorrowerState:
    """Terminal (pending): human agent takes over; graph ends here."""
    log.info(f"Borrower {state.borrower.borrower_id} handed off to human collections agent.")
    return state


def node_closed_paid(state: BorrowerState) -> BorrowerState:
    """Terminal: loan closed – payment received."""
    log.info(f"✅ Loan {state.borrower.loan_id} CLOSED (PAID) for {state.borrower.name}.")
    return state


def node_closed_disputed(state: BorrowerState) -> BorrowerState:
    """Terminal: dispute lodged – legal / compliance review."""
    log.info(
        f"⚠️  Loan {state.borrower.loan_id} CLOSED (DISPUTED) – "
        f"Reason: {state.dispute_reason}"
    )
    return state


# ═══════════════════════════════════════════════
# 6. CONDITIONAL ROUTING EDGES
# ═══════════════════════════════════════════════

def route_after_due_date_check(state: BorrowerState) -> str:
    if state.current_state == CollectionState.CLOSED_PAID:
        return "closed_paid"
    return "delinquent_d1"


def route_after_d1(state: BorrowerState) -> str:
    if state.current_state == CollectionState.CLOSED_PAID:
        return "closed_paid"
    return "delinquent_d3"


def route_after_d3(state: BorrowerState) -> str:
    mapping = {
        CollectionState.CLOSED_PAID:      "closed_paid",
        CollectionState.CLOSED_DISPUTED:  "closed_disputed",
        CollectionState.ESCALATED_HUMAN:  "escalated_human",
    }
    return mapping.get(state.current_state, "escalated_human")


# ═══════════════════════════════════════════════
# 7a. LANGGRAPH GRAPH BUILDER
# ═══════════════════════════════════════════════

def build_langgraph() -> "StateGraph":
    """
    Builds and compiles the LangGraph state machine.
    Each node is a deterministic Python function; edges are conditional.
    """
    if not LANGGRAPH_AVAILABLE:
        raise RuntimeError("langgraph is not installed.")

    builder = StateGraph(BorrowerState)

    # Register nodes
    builder.add_node("pending_d15",      node_d15_pending)
    builder.add_node("reminder_d7",      node_d7_reminder)
    builder.add_node("final_reminder_d1", node_d1_final_reminder)
    builder.add_node("due_date_check",   node_due_date_check)
    builder.add_node("delinquent_d1",    node_delinquent_d1)
    builder.add_node("delinquent_d3",    node_delinquent_d3)
    builder.add_node("escalated_human",  node_escalated_human)
    builder.add_node("closed_paid",      node_closed_paid)
    builder.add_node("closed_disputed",  node_closed_disputed)

    # Linear edges (deterministic progression)
    builder.add_edge(START,             "pending_d15")
    builder.add_edge("pending_d15",     "reminder_d7")
    builder.add_edge("reminder_d7",     "final_reminder_d1")
    builder.add_edge("final_reminder_d1", "due_date_check")

    # Conditional edges (payment checks & call outcomes)
    builder.add_conditional_edges(
        "due_date_check",
        route_after_due_date_check,
        {"closed_paid": "closed_paid", "delinquent_d1": "delinquent_d1"},
    )
    builder.add_conditional_edges(
        "delinquent_d1",
        route_after_d1,
        {"closed_paid": "closed_paid", "delinquent_d3": "delinquent_d3"},
    )
    builder.add_conditional_edges(
        "delinquent_d3",
        route_after_d3,
        {
            "closed_paid":     "closed_paid",
            "closed_disputed": "closed_disputed",
            "escalated_human": "escalated_human",
        },
    )

    # Terminal nodes → END
    for terminal in ("escalated_human", "closed_paid", "closed_disputed"):
        builder.add_edge(terminal, END)

    return builder.compile()


# ═══════════════════════════════════════════════
# 7b. PURE-PYTHON FALLBACK (no langgraph dependency)
# ═══════════════════════════════════════════════

PIPELINE_NODES = [
    node_d15_pending,
    node_d7_reminder,
    node_d1_final_reminder,
    node_due_date_check,
    node_delinquent_d1,
    node_delinquent_d3,
]

TERMINAL_STATES = {
    CollectionState.CLOSED_PAID,
    CollectionState.CLOSED_DISPUTED,
    CollectionState.ESCALATED_HUMAN,
}


def run_pipeline_python(state: BorrowerState) -> BorrowerState:
    """
    Pure-Python sequential runner – equivalent logic to the LangGraph graph.
    Used as a fallback when langgraph is not installed.
    """
    for node_fn in PIPELINE_NODES:
        state = node_fn(state)
        if state.current_state in TERMINAL_STATES:
            break

    # Run terminal node for final logging
    if state.current_state == CollectionState.CLOSED_PAID:
        state = node_closed_paid(state)
    elif state.current_state == CollectionState.CLOSED_DISPUTED:
        state = node_closed_disputed(state)
    elif state.current_state == CollectionState.ESCALATED_HUMAN:
        state = node_escalated_human(state)

    return state


# ═══════════════════════════════════════════════
# 8. MOCK WEBHOOK LISTENER
# ═══════════════════════════════════════════════

def simulate_payment_webhook(loan_id: str, borrower_state: BorrowerState) -> BorrowerState:
    """
    Simulates an inbound payment webhook from the payment gateway.
    In production this would be a FastAPI endpoint receiving a signed JWT payload.
    """
    log.info(f"WEBHOOK RECEIVED: Payment for loan {loan_id}")
    borrower_state.is_paid = True
    borrower_state.transition_to(
        CollectionState.CLOSED_PAID,
        f"Payment webhook received for loan {loan_id}.",
    )
    return borrower_state


# ═══════════════════════════════════════════════
# 9. AUDIT LOG EXPORT
# ═══════════════════════════════════════════════

def export_audit_log(state: BorrowerState) -> dict:
    """
    Exports a structured audit log for regulatory compliance (RBI IT Framework).
    PII is tokenised before export in a production system.
    """
    return {
        "run_id":          state.run_id,
        "loan_id":         state.borrower.loan_id,
        "borrower_id":     state.borrower.borrower_id,   # token, not name
        "final_state":     state.current_state.value,
        "is_paid":         state.is_paid,
        "is_disputed":     state.is_disputed,
        "dispute_reason":  state.dispute_reason,
        "contact_attempts": len(state.contact_history),
        "contacts": [
            {
                "timestamp": c.timestamp,
                "channel":   c.channel.value,
                "state":     c.state_at_time.value,
                "outcome":   c.outcome,
            }
            for c in state.contact_history
        ],
        "state_transitions": state.notes,
        "voice_call_transcript": state.voice_call_transcript,
    }


# ═══════════════════════════════════════════════
# 10. CLI DEMO
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import sys as _sys
    # Ensure UTF-8 output on Windows (handles the ₹ rupee symbol, etc.)
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Create a sample borrower
    borrower = BorrowerProfile(
        borrower_id="BRW-2024-00142",
        name="Arjun Mehta",
        phone="+91-98765-43210",
        email="arjun.mehta@example.com",
        loan_id="LN-2024-00891",
        outstanding_amount=18500.00,
        due_date=date(2024, 6, 30),
        language_preference="en",
    )

    initial_state = BorrowerState(borrower=borrower)

    print("\n" + "═" * 60)
    print("  COLLECTIONS ORCHESTRATOR – STATE MACHINE DEMO")
    print("═" * 60)
    print(f"  Borrower : {borrower.name}  ({borrower.borrower_id})")
    print(f"  Loan     : {borrower.loan_id}")
    print(f"  Amount   : ₹{borrower.outstanding_amount:,.2f}")
    print(f"  Due Date : {borrower.due_date}")
    print("═" * 60 + "\n")

    # ── Run the state machine ──────────────────
    if LANGGRAPH_AVAILABLE:
        print("Running via LangGraph …\n")
        graph = build_langgraph()
        raw = graph.invoke(initial_state)
        # LangGraph returns a dict when the state schema is a dataclass.
        # Unwrap it back to a BorrowerState so the rest of the code works.
        if isinstance(raw, dict):
            final_state = raw.get("__root__", None) or initial_state
            # Merge the dict fields back into the original state object
            for k, v in raw.items():
                if hasattr(initial_state, k):
                    setattr(initial_state, k, v)
            final_state = initial_state
        else:
            final_state = raw
    else:
        print("Running via pure-Python fallback …\n")
        final_state = run_pipeline_python(initial_state)

    # ── Print audit log ────────────────────────
    audit = export_audit_log(final_state)
    print("\n" + "─" * 60)
    print("AUDIT LOG")
    print("─" * 60)
    print(json.dumps(audit, indent=2, default=str))

    # ── Print the D+3 system prompt ────────────
    print("\n" + "─" * 60)
    print("D+3 VOICE AGENT SYSTEM PROMPT")
    print("─" * 60)
    prompt = D3_VOICE_AGENT_SYSTEM_PROMPT.replace(
        "{amount}", f"{borrower.outstanding_amount:,.2f}"
    )
    print(prompt)
    print("─" * 60 + "\n")
