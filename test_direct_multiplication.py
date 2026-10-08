"""One direct-answer multiplication call, with no supplied answer or CoT prompt."""
import json
import os
from openai import OpenAI

def main():
    a, b = 787897987897, 787897897899
    expected = a * b
    print(f"Python exact answer: {expected}", flush=True)
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set in this terminal.")
    client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL"), timeout=60, max_retries=0)
    response = client.chat.completions.create(
        model="gpt-4.1-mini-2025-04-14",
        messages=[{"role": "user", "content":
                   f"{a} * {b} = ? Only give the integer result, no explanation."}],
        temperature=0, max_tokens=64,
    )
    message = response.choices[0].message
    answer = (message.content or "").strip()
    print(f"Model answer: {answer}")
    print(f"Exact text match: {answer == str(expected)}")
    print(json.dumps({"model": response.model,
                      "finish_reason": response.choices[0].finish_reason,
                      "usage": response.usage.model_dump() if response.usage else None,
                      "reasoning_content": getattr(message, "reasoning_content", None)}, indent=2))

if __name__ == "__main__":
    main()
