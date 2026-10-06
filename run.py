"""
Thai E-Commerce Multi-Agent Customer Support System
Main Entry Point

Researcher: XINGYUCHEN | Student ID: 2505310001
Stamford International University — M.Sc. DIT
Academic Year 2026

Usage:
  python run.py server     # Start web chat interface
  python run.py simulate   # Run 120-scenario simulation
  python run.py init       # Initialize databases only
"""
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Load .env BEFORE any setdefault calls
from dotenv import load_dotenv
load_dotenv(os.path.join(project_root, ".env"))

os.environ.setdefault("DEEPSEEK_API_KEY", "")
os.environ.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


def main():
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   An LLM-Powered Multi-Agent Framework                     ║")
    print("║   for Automated Customer Support in Thai E-commerce        ║")
    print("║                                                            ║")
    print("║   Stamford International University                        ║")
    print("║   M.Sc. Digital and Information Technology                 ║")
    print("║   Researcher: XINGYUCHEN (2505310001)                      ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python run.py server     Start the web chat interface")
        print("  python run.py simulate   Run 120-scenario simulation")
        print("  python run.py init       Initialize databases only")
        return

    command = sys.argv[1]

    if command == "server":
        _start_server()
    elif command == "simulate":
        _run_simulation()
    elif command == "init":
        _init_databases()
    else:
        print(f"Unknown command: {command}")
        print("Use: server, simulate, or init")


def _init_databases():
    """Initialize the order database."""
    from app.db.migrations import upgrade_database
    from app.db.orders import init_database
    from app.config import STORE_DB_PATH
    db_path = STORE_DB_PATH
    print("Initializing order database...")
    upgrade_database()
    init_database(db_path)
    print(f"Done! Orders DB: {db_path}")


def _warm_up_policy_index():
    """Pre-load the policy embedding model + ChromaDB collection at startup.

    Starting the server without this makes the FIRST policy-search request pay
    the full cold-start cost (up to ~80s on a cold disk/network). Warming up
    here moves that cost to boot time. Failures are non-fatal.
    """
    import time
    try:
        from app.agents import policy_index
        t0 = time.perf_counter()
        print("Warming up policy index (embedding model)...")
        ok = policy_index.warm_up()
        if ok:
            elapsed = time.perf_counter() - t0
            print(f"Policy embedding model ready in {elapsed:.1f}s")
        else:
            print("WARNING: policy embedding model warm-up failed "
                  "(will lazy-load on first request)")
    except Exception as exc:  # pragma: no cover — startup must not break
        print(f"WARNING: policy index warm-up skipped: {exc}")


def _start_server():
    """Start the FastAPI web server."""
    _init_databases()
    _warm_up_policy_index()
    from app.api.server import app
    import uvicorn
    print("\n" + "─" * 50)
    print("Starting web server at http://localhost:8000")
    print("Open your browser to use the chat interface.")
    print("─" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)


def _run_simulation():
    """Run comparative simulation."""
    from simulation.simulation_runner import run_simulation
    print("Running comparative simulation (120 scenarios)...")
    print("NOTE: This requires a DeepSeek API key to work.")
    print()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("WARNING: DEEPSEEK_API_KEY not set!")
        print("Set environment variable or edit app/config.py")
        print("Simulation will use fallback keyword routing only.\n")

    run_simulation()


if __name__ == "__main__":
    main()
