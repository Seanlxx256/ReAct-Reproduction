"""Extend an existing QA run without repeating its completed sample prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import run_qa as qa


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", choices=("hotpotqa", "fever"), default="hotpotqa")
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--methods", nargs="+", choices=qa.METHODS, default=["standard", "cot", "act", "react"])
    parser.add_argument("--prepare-only", action="store_true", help="Update the target without calling the model")
    args = parser.parse_args()
    output = args.output.resolve()
    data, _, data_path, prompt_path = qa.load_task(args.dataset)
    if not 1 <= args.samples <= len(data):
        parser.error("Invalid target sample count")
    paths = [data_path, prompt_path, qa.ROOT / "run_qa.py", qa.ROOT / "run_hotpotqa.py",
             qa.ROOT / "wikienv.py", qa.ROOT / "wrappers.py"]
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    jobs = []
    common = None
    for method in dict.fromkeys(args.methods):
        folder = output / args.dataset / method
        config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        if config["hashes"] != hashes or config["dataset"] != args.dataset or config["method"] != method:
            parser.error(f"Code, data, or experiment configuration changed: {method}")
        indices = list(range(len(data)))
        random.Random(config["seed"]).shuffle(indices)
        old = config["indices"]
        if len(old) > args.samples or old != indices[:len(old)]:
            parser.error(f"Target must extend the original shuffled sample prefix: {method}")
        comparable = {k: v for k, v in config.items() if k not in ("method", "indices")}
        if common is not None and comparable != common:
            parser.error("Method configurations differ")
        common = comparable
        records = [json.loads(s) for s in (folder / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
        if [r["question_idx"] for r in records] != old[:len(records)]:
            parser.error(f"Saved sample order differs: {method}")
        jobs.append((folder, config, indices[:args.samples], records))
    # Finish all validation before changing any files. Keep the original configs.
    for folder, config, indices, records in jobs:
        backup = folder / f"config.before-{len(config['indices'])}.json"
        if not backup.exists() and len(config["indices"]) != len(indices):
            qa.write_json(backup, config)
        qa.write_json(folder / "config.json", {**config, "indices": indices})
        qa.write_json(folder / "summary.json", qa.summarize(records, args.samples, args.dataset))
    qa.write_json(output / args.dataset / "comparison.json", {
        config["method"]: qa.summarize(records, args.samples, args.dataset)
        for _, config, _, records in jobs})
    command = [sys.executable, str(qa.ROOT / "run_qa.py"), "--dataset", args.dataset,
               "--methods", *args.methods, "--samples", str(args.samples), "--output", str(output),
               "--model", common["model"], "--seed", str(common["seed"]),
               "--max-steps", str(common["max_steps"]), "--max-tokens", str(common["max_tokens"]),
               "--cot-max-tokens", str(common["cot_max_tokens"]), "--sc-samples", str(common["sc_samples"]), "--resume"]
    if common["base_url"]:
        command += ["--base-url", common["base_url"]]
    print(f"Target updated to {args.samples}; completed samples will be skipped.", flush=True)
    if not args.prepare_only:
        raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
