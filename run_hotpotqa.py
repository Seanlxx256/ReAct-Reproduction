"""Run the original six-shot HotpotQA ReAct prompt with a chat model."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
INSTRUCTION = """Solve a question answering task with interleaving Thought, Action, Observation steps. Thought can reason about the current situation, and Action can be three types: 
(1) Search[entity], which searches the exact entity on Wikipedia and returns the first paragraph if it exists. If not, it will return some similar entities to search.
(2) Lookup[keyword], which returns the next sentence containing keyword in the current passage.
(3) Finish[answer], which returns the answer and finishes the task.
Here are some examples.
"""
CHAT_INSTRUCTION = (
    "Continue the supplied transcript at its final unfinished line. "
    "Output only the continuation, without repeating examples or the question. "
    "If the final line starts with Thought, generate one Thought and one Action for the current step. "
    "If it starts with Action, generate only one action such as Search[entity], Lookup[keyword], or Finish[answer]. "
    "Do not generate an Observation; observations come from the environment."
)


class ChatLLM:
    def __init__(self, args):
        from openai import OpenAI
        self.client = OpenAI(base_url=args.base_url, timeout=60, max_retries=3)
        self.model = args.model
        self.max_tokens = args.max_tokens
        self.calls = []

    def __call__(self, prompt, stop, *, system=CHAT_INSTRUCTION, temperature=0, max_tokens=None):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens or self.max_tokens, top_p=1,
            frequency_penalty=0, presence_penalty=0, stop=stop,
        )
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("Model returned no text; check model mode and output budget.")
        self.calls.append({
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "finish_reason": response.choices[0].finish_reason,
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "text": text,
        })
        return text


def environment_step(env, action):
    import requests
    for attempt in range(3):
        try:
            return env.step(action)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def parse_action(raw, step):
    """Accept an explicit Action line or bare action, never model observations."""
    text = raw.strip()
    text = re.sub(rf"^Thought\s+{step}:\s*", "", text)
    match = re.search(rf"(?m)^\s*Action(?:\s+{step})?:[ \t]*(.*)$", text)
    if match:
        thought = text[:match.start()].strip()
        action = match.group(1).strip()
    else:
        thought, action = text, text
    # One complete action; do not salvage a truncated argument or hallucinated observation.
    if not re.fullmatch(r"(?:search|lookup|finish|think)\[[^\r\n]*\]", action, re.I):
        return thought.split("\nObservation", 1)[0], ""
    return thought if match else "", action


def run_episode(env, llm, idx, prompt, max_steps, act_only=False):
    question = env.reset(idx=idx)
    transcript = prompt + question + "\n"
    badcalls = 0
    steps = []
    done = False
    for i in range(1, max_steps + 1):
        prefix = "Action" if act_only else "Thought"
        raw = llm(transcript + f"{prefix} {i}:", [f"\nObservation {i}:"])
        thought, action = parse_action(raw, i)
        if not action:
            badcalls += 1
            repair_prompt = transcript
            if not act_only:
                repair_prompt += f"Thought {i}: {thought}\n"
            repair = llm(repair_prompt + f"Action {i}:", ["\n"])
            _, action = parse_action(repair, i)
        normalized_action = action[:1].lower() + action[1:]
        obs, reward, done, info = environment_step(env, normalized_action)
        obs = obs.replace("\\n", "")
        if not act_only:
            transcript += f"Thought {i}: {thought}\n"
        transcript += f"Action {i}: {action}\nObservation {i}: {obs}\n"
        steps.append({"thought": "" if act_only else thought, "action": action, "observation": obs})
        if done:
            break
    forced_finish = not done
    if forced_finish:
        obs, reward, done, info = environment_step(env, "finish[]")
        steps.append({"thought": "", "action": "finish[]", "observation": obs})
    return {**info, "question": question, "steps_trace": steps, "traj": transcript,
            "n_badcalls": badcalls, "forced_finish": forced_finish}


def write_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4.1-mini-2025-04-14")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"))
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--seed", type=int, default=233)
    parser.add_argument("--max-steps", type=int, default=7)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "hotpotqa-mini")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true", help="Validate data and dependencies without network/API calls")
    parser.add_argument("--check-wiki", action="store_true", help="Test Wikipedia retrieval only; no model API calls")
    args = parser.parse_args()
    os.chdir(ROOT)
    data_path = ROOT / "data/hotpot_dev_v1_simplified.json"
    prompt_path = ROOT / "prompts/prompts_naive.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    prompt = INSTRUCTION + json.loads(prompt_path.read_text(encoding="utf-8"))["webthink_simple6"]
    if not 1 <= args.samples <= len(data) or args.max_steps < 1 or args.max_tokens < 1:
        parser.error("Invalid samples, max-steps, or max-tokens")
    import wikienv
    import wrappers
    from openai import OpenAI  # Check SDK before creating an output directory.
    env = wrappers.HotPotQAWrapper(wikienv.WikiEnv(), split="dev")
    indices = list(range(len(data)))
    random.Random(args.seed).shuffle(indices)
    indices = indices[:args.samples]
    if args.check_wiki:
        wiki = wikienv.WikiEnv()
        obs, _, _, _ = wiki.step("search[Stuart Bird]")
        if not obs.strip():
            raise RuntimeError("Wikipedia returned an empty observation.")
        print("Wikipedia retrieval OK (no model API calls):\n" + obs[:500])
        return
    if args.check:
        print(json.dumps({"data_count": len(data), "model": args.model,
                          "sample_indices": indices, "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
                          "dependencies": "OK", "network_calls": 0}, indent=2))
        return
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in your local environment before running.")
    config = {"model": args.model, "base_url": args.base_url,
              "seed": args.seed, "indices": indices, "max_steps": args.max_steps,
              "max_tokens": args.max_tokens, "temperature": 0,
              "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
              "prompt_sha256": hashlib.sha256((CHAT_INSTRUCTION + prompt).encode()).hexdigest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "wiki_sha256": hashlib.sha256((ROOT / "wikienv.py").read_bytes()).hexdigest(),
              "wrappers_sha256": hashlib.sha256((ROOT / "wrappers.py").read_bytes()).hexdigest(),
              "retrieval": "live Wikipedia HTML"}
    output = args.output.resolve()
    config_file = output / "config.json"
    records_file = output / "episodes.jsonl"
    records = []
    if args.resume:
        if not config_file.exists() or json.loads(config_file.read_text(encoding="utf-8")) != config:
            parser.error("Resume configuration differs or is missing; use a new output directory.")
        if records_file.exists():
            records = [json.loads(line) for line in records_file.read_text(encoding="utf-8").splitlines()]
        completed = [r["question_idx"] for r in records]
        if completed != indices[:len(completed)]:
            parser.error("Saved episodes do not match the selected sample order.")
    else:
        if output.exists() and any(output.iterdir()):
            parser.error("Output directory is not empty; use --resume or a new --output.")
        output.mkdir(parents=True, exist_ok=True)
        write_json(config_file, config)
    llm = ChatLLM(args)
    for idx in indices[len(records):]:
        start = time.monotonic()
        llm.calls = []
        record = run_episode(env, llm, idx, prompt, args.max_steps)
        record.update({"calls": llm.calls, "n_calls": len(llm.calls),
                       "elapsed_seconds": time.monotonic() - start,
                       "timestamp_utc": datetime.now(timezone.utc).isoformat()})
        with records_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        records.append(record)
        summary = {"completed": len(records), "planned": len(indices),
                   "em": sum(r["em"] for r in records) / len(records),
                   "f1": sum(r["f1"] for r in records) / len(records),
                   "n_calls": sum(r["n_calls"] for r in records)}
        write_json(output / "summary.json", summary)
        print(f"{len(records)}/{len(indices)} idx={idx} EM={summary['em']:.4f} F1={summary['f1']:.4f}", flush=True)
    print(f"Results: {output}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted. Completed episodes are saved; rerun with --resume.", file=sys.stderr)
        sys.exit(130)
