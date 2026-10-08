"""Record the narrow Wikipedia Unicode fix in existing resume configs."""
import argparse
import hashlib
import json
from pathlib import Path

OLD_HASH = "0f84eba8f9b3c7375b216777a7853b383ba3061cd258962aafe21be11fae8bf6"
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--memory-fix", action="store_true")
    args = parser.parse_args()
    old_expected = "87c163185da335e2240dceb1669fb8d7d70151949ae7cc1f069622a579008675" if args.memory_fix else OLD_HASH
    label = "wiki-memory-fix" if args.memory_fix else "wiki-unicode-fix"
    new_hash = hashlib.sha256((ROOT / "wikienv.py").read_bytes()).hexdigest()
    jobs = []
    for path in sorted(args.output.glob("hotpotqa/*/config.json")):
        config = json.loads(path.read_text(encoding="utf-8"))
        old_hash = config["hashes"]["wikienv.py"]
        if old_hash == new_hash:
            continue
        if old_hash != old_expected:
            parser.error(f"Unexpected Wikipedia version: {path}")
        jobs.append((path, config))
    for path, config in jobs:
        backup = path.with_name(f"config.before-{label}.json")
        if backup.exists():
            raise RuntimeError(f"Backup already exists: {backup}")
        backup.write_bytes(path.read_bytes())
        records = path.with_name("episodes.jsonl")
        completed = len(records.read_text(encoding="utf-8").splitlines()) if records.exists() else 0
        path.with_name(f"{label}.json").write_text(json.dumps({
            "old_hash": old_expected, "new_hash": new_hash,
            "completed_before_fix": completed,
            "change": "Release HTML trees and bound ambiguous-page recursion to three requests" if args.memory_fix else "Keep original page text when legacy Unicode decoding fails",
        }, indent=2) + "\n", encoding="utf-8")
        config["hashes"]["wikienv.py"] = new_hash
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Updated {path}; preserved {completed} completed episodes")


if __name__ == "__main__":
    main()
