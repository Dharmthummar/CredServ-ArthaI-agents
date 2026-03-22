"""
Phase 1: Evidence-Based Onboarding Extractor (API-Less KYC)
============================================================
A VLM-powered microservice that extracts structured data from Indian
financial documents (bank statements).

Supported backends (both FREE, no subscription):
  --backend ollama   Local inference via Ollama (default, truly API-less)
                     Install: https://ollama.com  →  ollama pull llama3.2-vision
  --backend gemini   Google Gemini 1.5 Flash free tier (great for demos)
                     Get key: https://aistudio.google.com  (no credit card)

The "universal prompt" forces structured JSON output — no regex required.
A deterministic verification engine guardrail checks running balances and
triggers an automated retry loop on failure.

Usage:
    # Ollama (local, default)
    python extractor.py doc1.pdf doc2.pdf doc3.pdf

    # Gemini (cloud demo)
    $env:GEMINI_API_KEY="AIza..."
    python extractor.py --backend gemini doc1.pdf doc2.pdf doc3.pdf
"""

import argparse
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF – pip install pymupdf

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("kyc_extractor")


# ─────────────────────────────────────────────
# Backend constants
# ─────────────────────────────────────────────
BACKEND_OLLAMA = "ollama"
BACKEND_GEMINI = "gemini"

# Ollama model preference order (first installed one wins)
OLLAMA_PREFERRED_MODELS = ["llama3.2-vision", "llava", "moondream", "bakllava"]
OLLAMA_DEFAULT_MODEL = "llama3.2-vision"

# Gemini free-tier model
GEMINI_MODEL = "gemini-1.5-flash"


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────
@dataclass
class Transaction:
    date: str
    description: str
    debit: Optional[str]   # None if credit-only row
    credit: Optional[str]  # None if debit-only row
    balance: str


@dataclass
class BankStatement:
    account_holder_name: str
    bank_name: str
    account_number: str
    transactions: list[Transaction] = field(default_factory=list)

    # Metadata populated by the verification engine
    verification_passed: bool = False
    verification_notes: str = ""


@dataclass
class ExtractionResult:
    document_path: str
    statement: Optional[BankStatement]
    raw_llm_output: str
    retries_used: int
    success: bool
    backend_used: str = ""
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Universal extraction prompt  (backend-agnostic)
# ─────────────────────────────────────────────
EXTRACTION_SYSTEM_PROMPT = """
You are a world-class financial document OCR and parsing engine specialising in
Indian bank statements. You handle clean PDFs, skewed scans, watermarked documents,
and bilingual (English + Hindi/Marathi/Telugu) pages with equal accuracy.

YOUR SOLE OUTPUT IS A SINGLE VALID JSON OBJECT. No markdown, no prose, no commentary.

Schema (strict):
{
  "account_holder_name": "<full name exactly as printed>",
  "bank_name": "<bank name>",
  "account_number": "<account number, digits only>",
  "transactions": [
    {
      "date": "<DD-MM-YYYY or as printed>",
      "description": "<narration / reference>",
      "debit": "<amount as string with 2 decimal places, or null>",
      "credit": "<amount as string with 2 decimal places, or null>",
      "balance": "<running balance as string with 2 decimal places>"
    }
  ]
}

Rules:
1. If a field is missing from the document, use null – never fabricate data.
2. Monetary values must always be strings like "12345.67" (no commas, no currency symbols).
3. For bilingual content, extract the English transliteration when available; otherwise
   transliterate Devanagari/other script to English.
4. Preserve the exact transaction order as it appears in the document (chronological).
5. Return ONLY the JSON object – nothing else.
""".strip()

EXTRACTION_USER_PROMPT = """
Extract all structured data from the bank statement image(s) provided.
Follow the JSON schema in your system prompt exactly.
""".strip()

RETRY_CORRECTION_PROMPT = """
Your previous extraction failed the deterministic math verification check.

Failure reason: {failure_reason}

Here is your previous JSON output:
{previous_json}

Please re-examine the document carefully and produce a corrected JSON object.
Pay special attention to:
- Correct debit / credit amounts (do not mix them up)
- Correct running balance after each transaction
- Any rows you may have missed

Output ONLY the corrected JSON object.
""".strip()


# ─────────────────────────────────────────────
# PDF / Image loading
# ─────────────────────────────────────────────
def pdf_to_images_b64(pdf_path: Path, dpi: int = 200) -> list[str]:
    """Rasterise every page of a PDF to base-64 PNG strings."""
    doc = fitz.open(str(pdf_path))
    images_b64 = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        images_b64.append(base64.standard_b64encode(pix.tobytes("png")).decode())
    doc.close()
    return images_b64


def image_to_b64(image_path: Path) -> str:
    return base64.standard_b64encode(image_path.read_bytes()).decode()


def load_document(doc_path: Path) -> list[str]:
    """Load a PDF or image file; return list of base-64 PNG strings per page."""
    suffix = doc_path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_images_b64(doc_path)
    elif suffix in {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}:
        return [image_to_b64(doc_path)]
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# ═══════════════════════════════════════════
# BACKEND A: OLLAMA  (local, truly API-less)
# ═══════════════════════════════════════════
def _get_ollama_model() -> str:
    """Auto-detect the best installed Ollama vision model."""
    try:
        import ollama
        pulled = {m.model.split(":")[0] for m in ollama.list().models}
        for candidate in OLLAMA_PREFERRED_MODELS:
            if candidate in pulled:
                log.info(f"Ollama model selected: {candidate}")
                return candidate
        log.warning(
            f"No preferred model found {OLLAMA_PREFERRED_MODELS}. "
            f"Using '{OLLAMA_DEFAULT_MODEL}'. Run: ollama pull {OLLAMA_DEFAULT_MODEL}"
        )
        return OLLAMA_DEFAULT_MODEL
    except Exception:
        return OLLAMA_DEFAULT_MODEL


def _call_ollama(images_b64: list[str], user_text: str, model: str) -> str:
    """Call local Ollama VLM with page images."""
    import ollama
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user",   "content": user_text, "images": images_b64},
        ],
        options={"temperature": 0, "num_predict": 4096},
    )
    return response["message"]["content"]


# ═══════════════════════════════════════════
# BACKEND B: GOOGLE GEMINI  (free tier, demo)
# ═══════════════════════════════════════════
def _call_gemini(images_b64: list[str], user_text: str) -> str:
    """
    Call Google Gemini 1.5 Flash (free tier) with page images.
    Requires: pip install google-generativeai
              GEMINI_API_KEY environment variable
    Free tier: 15 req/min, 1,500 req/day — ample for this assignment.
    """
    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError(
            "google-generativeai not installed. Run: pip install google-generativeai"
        )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY environment variable not set.\n"
            "Get a free key at: https://aistudio.google.com"
        )

    genai.configure(api_key=api_key)

    # Auto-detect available flash model
    available_model = GEMINI_MODEL
    try:
        models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        flash_models = [m for m in models if "flash" in m.lower()]
        if flash_models:
            # pick the latest/first available one, removing 'models/' prefix if present
            # reverse sort so 2.0 comes before 1.5
            flash_models.sort(reverse=True)
            m_name = flash_models[0]
            available_model = m_name[7:] if m_name.startswith("models/") else m_name
            log.info(f"Auto-detected Gemini model: {available_model}")
    except Exception as e:
        log.warning(f"Could not list models: {e}")

    model = genai.GenerativeModel(
        model_name=available_model,
        system_instruction=EXTRACTION_SYSTEM_PROMPT,
    )

    # Build multimodal content: each page image + user text
    parts = []
    for img_b64 in images_b64:
        parts.append(
            {
                "mime_type": "image/png",
                "data": base64.b64decode(img_b64)
            }
        )
    parts.append(user_text)

    response = model.generate_content(
        parts,
        generation_config=genai.types.GenerationConfig(
            temperature=0,
            max_output_tokens=4096,
        ),
    )
    return response.text


# ─────────────────────────────────────────────
# Unified VLM dispatcher
# ─────────────────────────────────────────────
def call_vlm(
    images_b64: list[str],
    user_text: str,
    backend: str = BACKEND_OLLAMA,
    model: Optional[str] = None,
) -> str:
    """Route to the correct backend and return raw LLM text response."""
    if backend == BACKEND_GEMINI:
        return _call_gemini(images_b64, user_text)
    else:  # BACKEND_OLLAMA (default)
        m = model or _get_ollama_model()
        return _call_ollama(images_b64, user_text, m)


def call_vlm_retry(
    images_b64: list[str],
    previous_json: str,
    failure_reason: str,
    backend: str = BACKEND_OLLAMA,
    model: Optional[str] = None,
) -> str:
    """Retry: show the model its previous output + failure reason."""
    prompt = RETRY_CORRECTION_PROMPT.format(
        failure_reason=failure_reason,
        previous_json=previous_json,
    )
    return call_vlm(images_b64, prompt, backend, model)


# ─────────────────────────────────────────────
# JSON parsing
# ─────────────────────────────────────────────
def parse_json_response(raw: str) -> dict:
    """
    Extract and parse a JSON object from the LLM response.
    Handles accidental markdown fences that some models emit.
    """
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in LLM response.")
    return json.loads(cleaned[start:end])


def dict_to_statement(data: dict) -> BankStatement:
    """Convert a raw parsed dict into a BankStatement dataclass."""
    txns = [
        Transaction(
            date=t.get("date", ""),
            description=t.get("description", ""),
            debit=t.get("debit"),
            credit=t.get("credit"),
            balance=t.get("balance", "0.00"),
        )
        for t in data.get("transactions", [])
    ]
    return BankStatement(
        account_holder_name=data.get("account_holder_name", ""),
        bank_name=data.get("bank_name", ""),
        account_number=data.get("account_number", ""),
        transactions=txns,
    )


# ─────────────────────────────────────────────
# Deterministic Verification Engine  ← core assignment requirement
# ─────────────────────────────────────────────
def _to_decimal(value: Optional[str]) -> Decimal:
    """Safely parse a string to Decimal, returning 0 for None / blanks."""
    if value is None or str(value).strip() in {"", "null", "None"}:
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def verify_statement(statement: BankStatement) -> tuple[bool, str]:
    """
    Deterministic guardrail: verifies that every running balance is
    arithmetically consistent with debits and credits.

      expected_balance[i] = balance[i-1] – debit[i] + credit[i]

    Tolerance: 0.02 (2 paise) to account for printed rounding.
    Also performs an aggregate opening→closing check.

    Returns (passed: bool, notes: str).
    """
    txns = statement.transactions
    if not txns:
        return False, "No transactions found in extracted data."

    errors = []
    for i in range(1, len(txns)):
        prev_bal     = _to_decimal(txns[i - 1].balance)
        debit        = _to_decimal(txns[i].debit)
        credit       = _to_decimal(txns[i].credit)
        reported_bal = _to_decimal(txns[i].balance)
        expected_bal = prev_bal - debit + credit
        diff         = abs(expected_bal - reported_bal)

        if diff > Decimal("0.02"):
            errors.append(
                f"Row {i+1} ({txns[i].date}): "
                f"prev={prev_bal}, debit={debit}, credit={credit} → "
                f"expected={expected_bal:.2f}, reported={reported_bal:.2f}, diff={diff:.2f}"
            )

    if errors:
        return False, f"{len(errors)} balance mismatch(es):\n" + "\n".join(errors)

    # Aggregate check
    total_debit  = sum(_to_decimal(t.debit)   for t in txns)
    total_credit = sum(_to_decimal(t.credit)  for t in txns)
    opening      = (
        _to_decimal(txns[0].balance)
        + _to_decimal(txns[0].debit)
        - _to_decimal(txns[0].credit)
    )
    closing      = _to_decimal(txns[-1].balance)
    expected_closing = opening - total_debit + total_credit
    if abs(expected_closing - closing) > Decimal("0.02"):
        return False, (
            f"Aggregate check failed: opening≈{opening:.2f}, "
            f"total_debits={total_debit:.2f}, total_credits={total_credit:.2f}, "
            f"expected_closing={expected_closing:.2f}, actual_closing={closing:.2f}"
        )

    return True, (
        f"✓ All {len(txns)} rows pass. "
        f"Debits={total_debit:.2f}, Credits={total_credit:.2f}, "
        f"Closing balance={closing:.2f}."
    )


# ─────────────────────────────────────────────
# Main extraction pipeline with retry loop
# ─────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def extract_statement(
    doc_path: Path,
    backend: str = BACKEND_OLLAMA,
    model: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
) -> ExtractionResult:
    """
    Full pipeline:
    1. Load & rasterise the document.
    2. Call VLM (Ollama or Gemini) → parse JSON → build BankStatement.
    3. Run deterministic verifier.
    4. If verification fails, retry with the failure reason (up to max_retries).
    5. Return ExtractionResult with full audit trail.
    """
    log.info(f"Processing: {doc_path.name}  [backend={backend}]")
    images_b64 = load_document(doc_path)
    log.info(f"  Loaded {len(images_b64)} page(s).")

    # Resolve Ollama model once per document
    resolved_model = (model or _get_ollama_model()) if backend == BACKEND_OLLAMA else None

    raw_output  = ""
    statement: Optional[BankStatement] = None
    retries     = 0
    last_error  = None

    for attempt in range(max_retries + 1):
        try:
            if attempt == 0:
                log.info(f"  Attempt {attempt + 1}: Initial extraction …")
                raw_output = call_vlm(images_b64, EXTRACTION_USER_PROMPT, backend, resolved_model)
            else:
                log.info(f"  Attempt {attempt + 1}: Retry after verification failure …")
                raw_output = call_vlm_retry(
                    images_b64, raw_output, last_error or "Unknown error", backend, resolved_model
                )
                retries += 1
                time.sleep(RETRY_DELAY_SECONDS)

            data      = parse_json_response(raw_output)
            statement = dict_to_statement(data)
            log.info(f"  Parsed: {statement.account_holder_name} | {statement.bank_name}")

            passed, notes = verify_statement(statement)
            statement.verification_passed = passed
            statement.verification_notes  = notes

            if passed:
                log.info(f"  ✓ Verification PASSED on attempt {attempt + 1}.")
                return ExtractionResult(
                    document_path=str(doc_path),
                    statement=statement,
                    raw_llm_output=raw_output,
                    retries_used=retries,
                    success=True,
                    backend_used=backend,
                )
            else:
                last_error = notes
                log.warning(f"  ✗ Verification FAILED: {notes[:120]} …")

        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            last_error = f"Parsing error: {exc}"
            log.warning(f"  ✗ Parse error on attempt {attempt + 1}: {exc}")

    log.error(f"  All {max_retries} retries exhausted for {doc_path.name}.")
    return ExtractionResult(
        document_path=str(doc_path),
        statement=statement,
        raw_llm_output=raw_output,
        retries_used=retries,
        success=False,
        backend_used=backend,
        error=last_error,
    )


def process_documents(
    doc_paths: list[Path],
    backend: str = BACKEND_OLLAMA,
    model: Optional[str] = None,
) -> list[ExtractionResult]:
    """Process a batch of documents and return ExtractionResults."""
    results = []
    for path in doc_paths:
        result = extract_statement(path, backend=backend, model=model)
        results.append(result)
        _print_result(result)
    return results


def _print_result(result: ExtractionResult) -> None:
    print("\n" + "=" * 70)
    print(f"Document : {result.document_path}")
    print(f"Backend  : {result.backend_used}")
    print(f"Success  : {result.success}  |  Retries: {result.retries_used}")
    if result.statement:
        s = result.statement
        print(f"Holder   : {s.account_holder_name}")
        print(f"Bank     : {s.bank_name}")
        print(f"Account  : {s.account_number}")
        print(f"Txns     : {len(s.transactions)}")
        print(f"Verified : {s.verification_passed}")
        print(f"Notes    : {s.verification_notes}")
    if result.error:
        print(f"Error    : {result.error}")
    print("=" * 70)


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="KYC Bank Statement Extractor — free VLM backends"
    )
    parser.add_argument(
        "documents",
        nargs="+",
        help="PDF or image files to process",
    )
    parser.add_argument(
        "--backend",
        choices=[BACKEND_OLLAMA, BACKEND_GEMINI],
        default=BACKEND_OLLAMA,
        help=(
            f"VLM backend to use. "
            f"'{BACKEND_OLLAMA}' = local (no API key). "
            f"'{BACKEND_GEMINI}' = Google free tier (set GEMINI_API_KEY)."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override Ollama model name (e.g. moondream). Ignored for Gemini.",
    )
    args = parser.parse_args()

    paths = [Path(p) for p in args.documents]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"File(s) not found: {missing}")
        raise SystemExit(1)

    if args.backend == BACKEND_GEMINI and not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: Set GEMINI_API_KEY before using the gemini backend.")
        print("  Get a free key at: https://aistudio.google.com")
        raise SystemExit(1)

    process_documents(paths, backend=args.backend, model=args.model)
