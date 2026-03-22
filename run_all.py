import os
import subprocess
import sys

def print_header(title):
    print(f"\n{'-'*60}")
    print(f"🚀 {title}")
    print(f"{'-'*60}\n")

def run_script(script_path, args=None):
    if args is None:
        args = []
    cmd = [sys.executable, script_path] + args
    print(f">> Executing: {script_path} {' '.join(args)}")
    try:
        # Run process and stream output
        process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
        process.wait()
        if process.returncode != 0:
            print(f"❌ Error running {script_path}")
            sys.exit(process.returncode)
    except KeyboardInterrupt:
        process.terminate()
        sys.exit(1)

if __name__ == "__main__":
    print("============================================================")
    print("   CredServ ArthaI Agents — Full Pipeline Runner")
    print("============================================================\n")

    # 1. Run Tests (Optional but good for checking stability)
    print_header("Running Test Suite (Pytest)")
    try:
        subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--disable-warnings"], check=False)
    except Exception as e:
        print(f"Warning: Tests failed or pytest not installed: {e}")

    # 2. Generate Synthetic Documents
    print_header("Phase 1: Generating Synthentic Bank Statements")
    run_script(os.path.join("phase1", "generate_synthetic_docs.py"))

    # 3. Running Extractor
    print_header("Phase 1: KYC Extractor (VLM parsing + Verification Engine)")
    docs = [
        "synthetic_docs/doc_clean.pdf",
        "synthetic_docs/doc_degraded.pdf",
        "synthetic_docs/doc_bilingual.pdf"
    ]
    # Optionally change '--backend gemini' to '--backend ollama' if you prefer local model
    run_script(os.path.join("phase1", "extractor.py"), docs + ["--backend", "gemini"])

    # 4. Collections Orchestrator
    print_header("Phase 2: Collections Orchestrator (LangGraph)")
    run_script(os.path.join("phase2", "collections_orchestrator.py"))

    # 5. Launch User Interface Dashboard
    print_header("Phase 4: Launching Dashboard")
    print("Dashboard available locally at:")
    print("👉 http://localhost:8000/index.html\n")
    print("Press Ctrl+C to stop the dashboard server and exit.")
    try:
        subprocess.run([sys.executable, "-m", "http.server", "8000"])
    except KeyboardInterrupt:
        print("\nShutdown complete.")
