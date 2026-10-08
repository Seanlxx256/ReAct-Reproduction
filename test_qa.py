import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import run_hotpotqa
import run_qa as qa


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def __call__(self, prompt, stop, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return next(self.outputs)


class QATests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(max_steps=7, sc_samples=3, max_tokens=256, cot_max_tokens=512)
        self.prompts = {k: "Examples\n" for k in ("standard", "cot", "act", "react")}

    def test_action_without_thought_and_fake_observation(self):
        self.assertEqual(run_hotpotqa.parse_action("Action 4: Search[Amy Poehler]\nObservation 4: invented", 4),
                         ("", "Search[Amy Poehler]"))
        self.assertEqual(run_hotpotqa.parse_action("Observation 4: Search[Fake]", 4)[1], "")
        self.assertEqual(run_hotpotqa.parse_action("Thought 4: long\nAction 4: Search[Amy", 4)[1], "")

    def test_parser_regressions_on_v3(self):
        # Replaying saved responses does not call the API or Wikipedia.
        path = qa.ROOT / "results/hotpotqa-mini-20-v3/episodes.jsonl"
        if not path.exists():
            self.skipTest("Historical v3 output unavailable")
        records = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
        record = next(r for r in records if r["question_idx"] == 1885)
        action_only = [c["text"] for c in record["calls"] if c["text"].startswith("Action 4:")]
        self.assertTrue(action_only)
        self.assertEqual(run_hotpotqa.parse_action(action_only[0], 4)[1], "Search[Amy Poehler]")

    def test_standard_no_gold_or_retrieval(self):
        llm = FakeLLM(["Paris"])
        record = qa.evaluate("standard", "hotpotqa", 0, [("Which city?", "SECRET GOLD")], self.prompts, None, llm, self.args)
        self.assertEqual(record["answer"], "Paris")
        self.assertNotIn("SECRET GOLD", llm.calls[0]["prompt"])
        self.assertEqual(llm.calls[0]["temperature"], 0)

    def test_cot_and_fever_label_validation(self):
        llm = FakeLLM(["Reasoning.\nAnswer: SUPPORTS"])
        result = qa.closed_answer(llm, "Claim", "", "fever", True, 0, 512)
        self.assertEqual(result["answer"], "SUPPORTS")
        self.assertEqual(qa.parse_answer("Answer: Probably SUPPORTS", "fever"), "")

    def test_sc_votes_and_empty_outputs(self):
        llm = FakeLLM(["Reason.\nAnswer: The Paris", "Reason.\nAnswer: paris", "unfinished"])
        result = qa.self_consistency(llm, "City?", "", "hotpotqa", 3, 512)
        self.assertEqual(result["votes"], 2)
        self.assertEqual(len(llm.calls), 3)
        self.assertTrue(all(c["temperature"] == 0.7 for c in llm.calls))
        blank = qa.self_consistency(FakeLLM(["unfinished"] * 3), "", "", "hotpotqa", 3, 512)
        self.assertEqual((blank["answer"], blank["votes"]), ("", 0))

    def test_low_confidence_switch(self):
        llm = FakeLLM(["Answer: A", "Answer: B", "Answer: C", "Found.\nAction 1: Finish[Paris]"])
        data, _, _, _ = qa.load_task("hotpotqa")
        import wrappers, wikienv
        env = wrappers.HotPotQAWrapper(wikienv.WikiEnv(), "dev")
        result = qa.evaluate("cot_sc_to_react", "hotpotqa", 0, data, self.prompts, env, llm, self.args)
        self.assertEqual([s["method"] for s in result["stages"]], ["cot_sc", "react"])
        self.assertEqual(result["answer"], "Paris")

    def test_confident_sc_does_not_switch(self):
        llm = FakeLLM(["Answer: Paris", "Answer: Paris", "Answer: Berlin"])
        result = qa.evaluate("cot_sc_to_react", "hotpotqa", 0, [("City?", "Paris")], self.prompts, None, llm, self.args)
        self.assertEqual(len(result["stages"]), 1)
        self.assertEqual(result["em"], 1)

    def test_react_timeout_switch(self):
        self.args.max_steps = 1
        llm = FakeLLM(["Plan.\nAction 1: think[plan]", "Answer: Paris", "Answer: Paris", "Answer: Berlin"])
        import wrappers, wikienv
        env = wrappers.HotPotQAWrapper(wikienv.WikiEnv(), "dev")
        result = qa.evaluate("react_to_cot_sc", "hotpotqa", 0, [("City?", "Paris")], self.prompts, env, llm, self.args)
        self.assertEqual([s["method"] for s in result["stages"]], ["react", "cot_sc"])
        self.assertEqual(result["answer"], "Paris")

    def test_act_has_no_thought_prompt(self):
        llm = FakeLLM(["Finish[Paris]"])
        import wrappers, wikienv
        env = wrappers.HotPotQAWrapper(wikienv.WikiEnv(), "dev")
        result = qa.evaluate("act", "hotpotqa", 0, [("City?", "Paris")], self.prompts, env, llm, self.args)
        self.assertTrue(llm.calls[0]["prompt"].endswith("Action 1:"))
        self.assertNotIn("Thought", llm.calls[0]["prompt"])
        self.assertEqual(result["stages"][0]["steps_trace"][0]["thought"], "")

    def test_all_methods_cli_and_resume(self):
        class FixtureLLM:
            def __init__(self, args):
                self.calls = []

            def __call__(self, prompt, stop, **kwargs):
                text = "Found.\nAction 1: Finish[Paris]" if kwargs.get("system") == qa.CHAT_INSTRUCTION else (
                    "Finish[Paris]" if kwargs.get("system") == qa.ACT_SYSTEM else "Answer: Paris")
                self.calls.append({"text": text, "finish_reason": "stop", "usage": {
                    "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                    "prompt_tokens_details": {"cached_tokens": 50}}})
                return text

        with tempfile.TemporaryDirectory() as tmp:
            argv = ["run_qa.py", "--methods", "all", "--samples", "2", "--sc-samples", "3", "--output", tmp]
            with patch.dict("os.environ", {"OPENAI_API_KEY": "offline"}), patch.object(qa, "ChatLLM", FixtureLLM):
                with patch("sys.argv", argv):
                    qa.main()
                selected = []
                for method in qa.METHODS:
                    folder = Path(tmp) / "hotpotqa" / method
                    records = [json.loads(s) for s in (folder / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
                    selected.append([r["question_idx"] for r in records])
                    self.assertEqual(len(records), 2)
                    self.assertEqual(json.loads((folder / "summary.json").read_text())["completed"], 2)
                self.assertTrue(all(ix == selected[0] for ix in selected))
                with patch("sys.argv", argv + ["--resume"]):
                    qa.main()
                self.assertEqual(len((Path(tmp) / "hotpotqa/react/episodes.jsonl").read_text(encoding="utf-8").splitlines()), 2)

    def test_fever_full_dataset(self):
        data, prompts, _, _ = qa.load_task("fever")
        self.assertEqual(len(data), 9999)
        self.assertEqual(set(prompts), {"standard", "cot", "act", "react"})


if __name__ == "__main__":
    unittest.main()
