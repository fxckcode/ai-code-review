#!/usr/bin/env python3
"""Set the OpenAI API key as an env var and run review."""
import os, sys, subprocess

# Full key — this file gets the actual key value
KEY = "sk-pro...zIEA"

os.environ["OPENAI_API_KEY"] = KEY
os.environ["REPO"] = "fxckcode/ai-code-review"
os.environ["PR_NUMBER"] = "1"

result = subprocess.run(
    [sys.executable, ".github/scripts/review.py"],
    capture_output=True, text=True, timeout=120,
    env=os.environ,
    cwd="/home/fxckcode/ai-code-review",
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
print(f"Exit: {result.returncode}")
