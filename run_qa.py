"""Question-only QA comparisons inspired by ReAct Table 1 (replacement model)."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import time

from run_hotpotqa import ROOT, INSTRUCTION, CHAT_INSTRUCTION, ChatLLM, run_episode, write_json
import wikienv
import wrappers

METHODS = ("standard", "cot", "act", "react", "cot_sc", "cot_sc_to_react", "react_to_cot_sc")
CLOSED_SYSTEM = (
    "Continue the supplied transcript for the final question or claim only. "
    "Do not repeat examples. Do not use external tools or invent observations. "
    "End with Answer: followed by only the concise answer or classification label."
)
ACT_SYSTEM = (
    "Continue the supplied transcript at its final Action line. "
    "Output only one action: Search[entity], Lookup[keyword], or Finish[answer]. "
    "Do not output thoughts or observations."
)


def load_task(dataset):
    if dataset == "hotpotqa":
        path = ROOT / "data/hotpot_dev_v1_simplified.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        data = [(r["question"], r["answer"]) for r in rows]
        prompts_path = ROOT / "prompts/prompts_naive.json"
        suffix = "6"
    else:
        path = ROOT / "data/paper_dev.jsonl"
        rows = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
        data = [(r["claim"], r["label"]) for r in rows]
        prompts_path = ROOT / "prompts/fever.json"
        suffix = "3"
    examples = json.loads(prompts_path.read_text(encoding="utf-8"))
    prompts = {k: examples[v + suffix] for k, v in {
        "standard": "webqa_simple", "cot": "cotqa_simple",
        "act": "webact_simple", "react": "webthink_simple"}.items()}
    if dataset == "hotpotqa":
        prompts["react"] = INSTRUCTION + prompts["react"]
    prompts["act"] = (
        "Solve the task using Search[entity], Lookup[keyword], and Finish[answer]. "
        "Generate actions only, without thoughts. Here are examples.\n" + prompts["act"]
    )
    if dataset == "fever":
        label_instruction = "Classify the claim as SUPPORTS, REFUTES, or NOT ENOUGH INFO.\n"
        prompts = {k: label_instruction + v for k, v in prompts.items()}
    return data, prompts, path, prompts_path


def answer_key(answer, dataset):
    # FEVER class strings must not lose the article in NOT ENOUGH INFO.
    if dataset == "fever":
        return " ".join(answer.upper().split())
    return wrappers.normalize_answer(answer)


def parse_answer(text, dataset):
    matches = re.findall(r"(?m)^\s*Answer:[ \t]*(.*)$", text)
    answer = matches[-1].strip() if matches else ""
    if dataset == "fever" and answer.upper() not in {"SUPPORTS", "REFUTES", "NOT ENOUGH INFO"}:
        return ""
    return answer


def closed_answer(llm, question, prompt, dataset, cot, temperature, max_tokens):
    task = ("Question: " if dataset == "hotpotqa" else "Claim: ") + question
    prefix = "Thought: Let's think step by step." if cot else "Answer:"
    raw = llm(prompt + task + "\n" + prefix, ["\nQuestion:", "\nClaim:"],
              system=CLOSED_SYSTEM, temperature=temperature, max_tokens=max_tokens)
    # Direct-answer completion may omit the unfinished Answer prefix.
    text = raw if cot or re.search(r"(?m)^\s*Answer:", raw) else "Answer: " + raw
    return {"answer": parse_answer(text, dataset), "text": raw}


def self_consistency(llm, question, prompt, dataset, trials, max_tokens):
    samples = [closed_answer(llm, question, prompt, dataset, True, 0.7, max_tokens)
               for _ in range(trials)]
    counts = Counter(answer_key(s["answer"], dataset) for s in samples if s["answer"].strip())
    winner, votes = counts.most_common(1)[0] if counts else ("", 0)
    answer = next((s["answer"] for s in samples if s["answer"].strip() and
                   answer_key(s["answer"], dataset) == winner), "")
    return {"answer": answer, "samples": samples, "votes": votes,
            "trials": trials, "tie_break": "first sampled answer"}


def evaluate(method, dataset, idx, data, prompts, env, llm, args):
    question, gold = data[idx]
    stages = []

    def interactive(kind):
        def generate(prompt, stop):
            return llm(prompt, stop, system=ACT_SYSTEM if kind == "act" else CHAT_INSTRUCTION)
        stage = run_episode(env, generate, idx, prompts[kind], args.max_steps, act_only=kind == "act")
        stages.append({"method": kind, **stage})
        return stage["answer"], stage["forced_finish"]

    def sc():
        stage = self_consistency(llm, question, prompts["cot"], dataset, args.sc_samples, args.cot_max_tokens)
        stages.append({"method": "cot_sc", **stage})
        return stage

    if method in ("act", "react"):
        answer, _ = interactive(method)
    elif method in ("standard", "cot"):
        stage = closed_answer(llm, question, prompts[method], dataset, method == "cot", 0,
                              args.cot_max_tokens if method == "cot" else args.max_tokens)
        stages.append({"method": method, **stage})
        answer = stage["answer"]
    elif method == "cot_sc":
        answer = sc()["answer"]
    elif method == "cot_sc_to_react":
        result = sc()
        answer = result["answer"]
        if result["votes"] * 2 < args.sc_samples:
            answer, _ = interactive("react")
    else:
        answer, timed_out = interactive("react")
        if timed_out:
            answer = sc()["answer"]
    em = int(answer_key(answer, dataset) == answer_key(gold, dataset))
    f1 = wrappers.f1_score(answer, gold)[0] if dataset == "hotpotqa" else em
    return {"question_idx": idx, "question": question, "gt_answer": gold,
            "answer": answer, "em": em, "f1": f1, "stages": stages}


def summarize(records, planned, dataset):
    calls = [c for r in records for c in r["calls"]]
    usage = {k: sum((c.get("usage") or {}).get(k, 0) for c in calls)
             for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    usage["cached_tokens"] = sum(((c.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                                 for c in calls)
    return {"completed": len(records), "planned": planned,
            "metric": "EM" if dataset == "hotpotqa" else "accuracy",
            "em": sum(r["em"] for r in records) / len(records) if records else None,
            "f1": sum(r["f1"] for r in records) / len(records) if records else None,
            "n_calls": len(calls), "usage": usage,
            "format_repairs": sum(s.get("n_badcalls", 0) for r in records for s in r["stages"]),
            "forced_finishes": sum(s.get("forced_finish", False) for r in records for s in r["stages"]),
            "truncated_calls": sum(c.get("finish_reason") == "length" for c in calls)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("hotpotqa", "fever"), default="hotpotqa")
    parser.add_argument("--methods", nargs="+", choices=(*METHODS, "all"), default=["standard", "cot", "act", "react"])
    parser.add_argument("--model", default="gpt-4.1-mini-2025-04-14")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"))
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--seed", type=int, default=233)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--cot-max-tokens", type=int, default=512)
    parser.add_argument("--sc-samples", type=int, default=21)
    parser.add_argument("--output", type=Path, default=ROOT / "results/qa-comparison")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    args.max_steps = args.max_steps if args.max_steps is not None else (7 if args.dataset == "hotpotqa" else 5)
    data, prompts, data_path, prompts_path = load_task(args.dataset)
    if not 1 <= args.samples <= len(data) or min(args.max_steps, args.max_tokens, args.cot_max_tokens, args.sc_samples) < 1:
        parser.error("Sample count or generation budgets are invalid")
    methods = list(METHODS) if "all" in args.methods else list(dict.fromkeys(args.methods))
    indices = list(range(len(data)))
    random.Random(args.seed).shuffle(indices)
    indices = indices[:args.samples]
    if args.check:
        print(json.dumps({"dataset": args.dataset, "data_count": len(data), "methods": methods,
                          "indices": indices, "max_steps": args.max_steps, "max_tokens": args.max_tokens,
                          "cot_max_tokens": args.cot_max_tokens, "sc_samples": args.sc_samples,
                          "api_key_configured": bool(os.getenv("OPENAI_API_KEY")), "network_calls": 0}, indent=2))
        return
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY before running")
    env = (wrappers.HotPotQAWrapper if args.dataset == "hotpotqa" else wrappers.FeverWrapper)(wikienv.WikiEnv(), split="dev")
    config = {"dataset": args.dataset, "model": args.model, "base_url": args.base_url,
              "indices": indices, "seed": args.seed, "max_steps": args.max_steps,
              "max_tokens": args.max_tokens, "cot_max_tokens": args.cot_max_tokens,
              "sc_samples": args.sc_samples, "sc_temperature": 0.7, "temperature": 0,
              "retrieval": "live Wikipedia HTML", "question_only": True}
    config["hashes"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                        (data_path, prompts_path, ROOT / "run_qa.py", ROOT / "run_hotpotqa.py",
                         ROOT / "wikienv.py", ROOT / "wrappers.py")}
    # Validate every requested directory before starting any paid calls.
    jobs = []
    for method in methods:
        output = args.output.resolve() / args.dataset / method
        expected = {**config, "method": method}
        records = []
        if output.exists() and any(output.iterdir()):
            cfg = output / "config.json"
            if not args.resume or not cfg.exists() or json.loads(cfg.read_text(encoding="utf-8")) != expected:
                parser.error(f"Existing configuration differs: {output}. Use a new --output or matching --resume.")
            path = output / "episodes.jsonl"
            if path.exists():
                records = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
            if [r["question_idx"] for r in records] != indices[:len(records)]:
                parser.error(f"Saved sample order differs: {output}")
        jobs.append((method, output, expected, records))
    llm = ChatLLM(args)
    for method, output, expected, records in jobs:
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "config.json", expected)
        for idx in indices[len(records):]:
            llm.calls = []
            start = time.monotonic()
            try:
                record = evaluate(method, args.dataset, idx, data, prompts, env, llm, args)
            except (Exception, KeyboardInterrupt) as error:
                # Keep returned usage for interrupted episodes without scoring them as failures.
                failure = {"question_idx": idx, "method": method, "error_type": type(error).__name__,
                           "calls": llm.calls, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
                with (output / "interruptions.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(failure, ensure_ascii=False) + "\n")
                raise
            record.update({"method": method, "dataset": args.dataset, "calls": llm.calls,
                           "elapsed_seconds": time.monotonic() - start,
                           "timestamp_utc": datetime.now(timezone.utc).isoformat()})
            with (output / "episodes.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            records.append(record)
            summary = summarize(records, len(indices), args.dataset)
            write_json(output / "summary.json", summary)
            print(f"{args.dataset}/{method} {len(records)}/{len(indices)} EM/Acc={summary['em']:.4f}", flush=True)
        write_json(output / "summary.json", summarize(records, len(indices), args.dataset))
    comparison = {method: summarize(records, len(indices), args.dataset)
                  for method, _, _, records in jobs}
    write_json(args.output.resolve() / args.dataset / "comparison.json", comparison)
    print(f"Results: {args.output.resolve() / args.dataset}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; completed samples saved. Rerun the same command with --resume.")
        raise SystemExit(130)
