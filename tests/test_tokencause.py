import tempfile
import unittest
import sqlite3
from pathlib import Path

from tokencause import analyze, load_codex_threads, parse_codex_rollout, load_jsonl, parse_event


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

    def test_codex_scan_and_rollout_explain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / ".codex"
            codex_home.mkdir()
            rollout = codex_home / "rollout.jsonl"
            rollout.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-06-12T00:00:00Z","type":"event_msg","payload":{"type":"user_message","message":"Please fix src/app.py"}}',
                        '{"timestamp":"2026-06-12T00:00:01Z","type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\\"cmd\\":\\"pytest tests/test_app.py\\"}","call_id":"c1"}}',
                        '{"timestamp":"2026-06-12T00:00:02Z","type":"response_item","payload":{"type":"function_call_output","call_id":"c1","output":"ERROR failed tests/test_app.py\\nTraceback\\n' + ("x" * 4000) + '"}}',
                        '{"timestamp":"2026-06-12T00:00:03Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":1000,"cached_input_tokens":500,"output_tokens":100,"total_tokens":1100}}}}',
                    ]
                ),
                encoding="utf-8",
            )
            db = codex_home / "state_5.sqlite"
            with sqlite3.connect(db) as connection:
                connection.execute(
                    "create table threads (id text, title text, rollout_path text, cwd text, updated_at integer, tokens_used integer)"
                )
                connection.execute(
                    "insert into threads values (?, ?, ?, ?, ?, ?)",
                    ("thread-1", "Fix app", str(rollout), str(root), 100, 1100),
                )

            threads = load_codex_threads(codex_home)
            report = parse_codex_rollout(threads[0])

        self.assertEqual(len(threads), 1)
        self.assertEqual(report.model_total_tokens, 1100)
        self.assertTrue(report.long_tool_outputs)
        self.assertTrue(report.failure_events)
        self.assertIn("tests/test_app.py", report.file_tokens)


if __name__ == "__main__":
    unittest.main()
