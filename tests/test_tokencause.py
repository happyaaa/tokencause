import tempfile
import unittest
from pathlib import Path

from tokencause import analyze, load_jsonl, parse_event


class TokenCauseTests(unittest.TestCase):
    def test_analyze_flags_budget_and_repeated_context(self):
        events = [
            parse_event(
                {
                    "step": "search_repo",
                    "model": "claude-fable-5",
                    "tool": "grep",
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cost_usd": 0.5,
                    "latency_ms": 1000,
                    "context_hash": "a",
                    "files": ["x.py"],
                },
                1,
            ),
            parse_event(
                {
                    "step": "fix",
                    "model": "claude-fable-5",
                    "input_tokens": 2000,
                    "output_tokens": 200,
                    "cost_usd": 0.7,
                    "latency_ms": 2000,
                    "context_hash": "a",
                    "files": ["x.py"],
                },
                2,
            ),
        ]
        result = analyze(events, budget_usd=1.0)
        titles = [finding.title for finding in result.findings]

        self.assertEqual(result.total_cost, 1.2)
        self.assertIn("超过预算", titles)
        self.assertIn("发现重复上下文", titles)
        self.assertIn("昂贵模型可能用于低价值步骤", titles)
        self.assertGreater(result.estimated_savings_usd, 0)

    def test_load_litellm_jsonl(self):
        payload = "\n".join(
            [
                '{"model":"claude-fable-5","response_cost":0.25,"usage":{"prompt_tokens":1000,"completion_tokens":100},"duration_ms":3000,"metadata":{"run_id":"r1","step":"search_docs","tool":"search","context_hash":"ctx","context_items":["a.md"]},"status":"success"}',
                '{"model":"claude-fable-5","response_cost":0.35,"usage":{"prompt_tokens":1200,"completion_tokens":100},"duration_ms":4000,"metadata":{"run_id":"r1","step":"search_docs","tool":"search","context_hash":"ctx","context_items":["a.md"]},"status":"success"}',
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "litellm.jsonl"
            path.write_text(payload, encoding="utf-8")
            events = load_jsonl(path, parser="litellm")

        result = analyze(events, budget_usd=0.5)
        titles = [finding.title for finding in result.findings]

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].step, "search_docs")
        self.assertEqual(events[0].cost_usd, 0.25)
        self.assertIn("超过预算", titles)
        self.assertTrue(result.recommendations)


if __name__ == "__main__":
    unittest.main()
