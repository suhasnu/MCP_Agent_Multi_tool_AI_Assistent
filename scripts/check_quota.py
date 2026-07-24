"""Show Groq's live rate-limit state.

Run:  python scripts/check_quota.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import httpx  # noqa: E402

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")


def main() -> None:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY is not set.")
        return

    print(f"Model: {MODEL}\n")

    resp = httpx.post(
        URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        },
        timeout=30,
    )

    print(f"HTTP {resp.status_code}\n")

    interesting = sorted(
        k for k in resp.headers if "ratelimit" in k.lower() or k.lower() == "retry-after"
    )
    if interesting:
        print("Rate limit headers:")
        for key_name in interesting:
            print(f"  {key_name:<34} {resp.headers[key_name]}")
    else:
        print("No rate limit headers returned.")

    if resp.status_code != 200:
        print("\nResponse body:")
        print(f"  {resp.text[:600]}")
        return

    print("\nInterpretation:")
    remaining_t = resp.headers.get("x-ratelimit-remaining-tokens")
    limit_t = resp.headers.get("x-ratelimit-limit-tokens")
    reset_t = resp.headers.get("x-ratelimit-reset-tokens")

    if remaining_t and limit_t:
        try:
            used = int(limit_t) - int(remaining_t)
            print(f"  Tokens used this window: {used:,} of {int(limit_t):,}")
            if int(remaining_t) < 2000:
                print("  LOW. An agent turn needs roughly 1500 to 2500 tokens.")
                print(f"  Resets in: {reset_t}")
            else:
                print("  Plenty of headroom. Rate limiting is not the cause.")
        except ValueError:
            pass

    remaining_r = resp.headers.get("x-ratelimit-remaining-requests")
    if remaining_r:
        print(f"  Requests remaining today: {remaining_r}")


if __name__ == "__main__":
    main()