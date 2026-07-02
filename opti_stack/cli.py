"""
CLI entry point for Opti-Stack.

This replaces the old agent.py, which reimplemented the entire pipeline
independently of app_ui.py and had already drifted out of sync with it
(different model names, no retry logic, no verification). This file
contains no business logic of its own -- it only calls the shared
orchestrator, so the CLI and the Streamlit UI can never drift apart again.

Usage:
    python -m opti_stack.cli path/to/script.py
    python -m opti_stack.cli --inline "print('hello')"
"""

import sys
import argparse

from .orchestrator import execute_pipeline


def main():
    parser = argparse.ArgumentParser(description="Opti-Stack: autonomous code optimizer.")
    parser.add_argument("script_path", nargs="?", help="Path to a Python script to optimize.")
    parser.add_argument("--inline", help="Inline Python source code as a string, instead of a file.")
    args = parser.parse_args()

    if args.inline:
        raw_script = args.inline
    elif args.script_path:
        with open(args.script_path) as f:
            raw_script = f.read()
    else:
        parser.error("Provide either a script_path or --inline.")
        return

    result = execute_pipeline(raw_script)

    if result.get("rejected_at_input"):
        print("\n=== REJECTED BEFORE EXECUTION ===")
        print("The security scanner rejected this script before any agent ran:")
        for v in result.get("security_violations", []):
            print(f"  - {v}")
        sys.exit(2)

    print("\n=== FINAL RESULT ===")
    print(f"Verified correct: {result['verified']}")
    if result["verified"]:
        print(f"\nOriginal benchmark:  {result['original_benchmark_summary']}")
        print(f"Optimized benchmark: {result['optimized_benchmark_summary']}")
        print("\n--- Scale sweep ---")
        for row in result.get("scale_sweep", []):
            print(row)
        print("\n--- Final optimized code ---")
        print(result["final_optimized_code"])
    else:
        print("Could not produce a verified optimization.")
        if result["steps"]:
            print("Last attempt:")
            print(result["steps"][-1].get("code", "(no code recorded)"))
        sys.exit(1)


if __name__ == "__main__":
    main()
