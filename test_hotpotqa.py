import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_hotpotqa as runner
import wikienv
import wrappers


class HotpotTests(unittest.TestCase):
    def test_wikipedia_literal_backslashes(self):
        for text in (r"A Windows path C:\Users\example", r"Equation \U", "中文 😀"):
            self.assertEqual(wikienv.clean_str(text), text)
        self.assertEqual(wikienv.clean_str("Ordinary Wikipedia text."), "Ordinary Wikipedia text.")

    def setUp(self):
        self.env = wrappers.HotPotQAWrapper(wikienv.WikiEnv(), split="dev")

    def test_finish_and_repeated_prefix(self):
        answer = self.env.data[0][1]
        outputs = iter([f"Thought 1: Known.\nAction 1: Finish[{answer}]"])
        record = runner.run_episode(self.env, lambda *args: next(outputs), 0, "", 7)
        self.assertEqual((record["em"], record["f1"]), (1, 1))
        self.assertFalse(record["forced_finish"])

    def test_fallback_and_step_limit(self):
        outputs = iter(["Need more information.", "Action 1: think[plan]"])
        record = runner.run_episode(self.env, lambda *args: next(outputs), 0, "", 1)
        self.assertEqual(record["n_badcalls"], 1)
        self.assertTrue(record["forced_finish"])
        self.assertEqual(record["em"], 0)

    def test_empty_action(self):
        outputs = iter(["Uncertain", ""])
        record = runner.run_episode(self.env, lambda *args: next(outputs), 0, "", 1)
        self.assertIn("Invalid action", record["steps_trace"][0]["observation"])

    def test_metrics(self):
        self.assertEqual(wrappers.normalize_answer("The Eiffel Tower!"), "eiffel tower")
        self.assertAlmostEqual(wrappers.f1_score("red blue", "red green")[0], 0.5)

    def test_wikipedia_identification_and_lookup(self):
        response = Mock()
        response.text = "<p>Stuart Bird was a public official. He worked in Ireland.</p>"
        wiki = wikienv.WikiEnv()
        with patch("wikienv.requests.get", return_value=response) as request:
            obs, _, _, _ = wiki.step("search[Stuart Bird & Ireland]")
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["params"], {"search": "Stuart Bird & Ireland"})
        self.assertIn("ReActReplication", kwargs["headers"]["User-Agent"])
        self.assertEqual(kwargs["timeout"], 30)
        response.raise_for_status.assert_called_once()
        self.assertIn("Stuart Bird", obs)
        lookup, _, _, _ = wiki.step("lookup[Ireland]")
        self.assertIn("He worked in Ireland", lookup)

    def test_ambiguous_search_is_bounded(self):
        response = Mock()
        response.text = "<p>This name may refer to: several different entities.</p>"
        wiki = wikienv.WikiEnv()
        with patch("wikienv.requests.get", return_value=response) as request:
            obs, _, _, _ = wiki.step("search[Ambiguous]")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(response.close.call_count, 3)
        self.assertIn("ambiguous Wikipedia page", obs)
        self.assertIsNone(wiki.page)

    def test_resume(self):
        class FakeLLM:
            def __init__(fake, args):
                fake.calls = []

            def __call__(fake, prompt, stop):
                fake.calls.append({"text": "offline fixture"})
                return "Done.\nAction 1: Finish[unknown]"

        with tempfile.TemporaryDirectory() as tmp:
            argv = ["run_hotpotqa.py", "--samples", "2", "--output", tmp]
            with patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test"}), patch.object(runner, "ChatLLM", FakeLLM):
                with patch("sys.argv", argv):
                    runner.main()
                rows_path = Path(tmp) / "episodes.jsonl"
                saved = rows_path.read_text(encoding="utf-8")
                indices = list(range(7405))
                random.Random(233).shuffle(indices)
                self.assertEqual([json.loads(line)["question_idx"] for line in saved.splitlines()], indices[:2])
                rows_path.write_text(saved.splitlines()[0] + "\n", encoding="utf-8")
                with patch("sys.argv", argv + ["--resume"]):
                    runner.main()
                self.assertEqual(len(rows_path.read_text(encoding="utf-8").splitlines()), 2)
                with patch("sys.argv", argv + ["--resume", "--seed", "1"]):
                    with self.assertRaises(SystemExit) as error:
                        runner.main()
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
