import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import agent  # noqa: E402


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, keep_open=False):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.killed = False
        self.returncode = returncode
        if stdout:
            self.stdout.feed_data(stdout)
        if stderr:
            self.stderr.feed_data(stderr)
        if not keep_open:
            self.stdout.feed_eof()
            self.stderr.feed_eof()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    async def wait(self):
        return self.returncode


async def collect(prompt, config):
    return "".join([chunk async for chunk in agent.stream_agent(prompt, config)])


class StreamAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_quoted_command_model_and_effort_are_preserved(self):
        process = FakeProcess(stdout=b"complete")
        captured = {}

        async def create_process(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return process

        config = {
            "command": '"/opt/Agent CLI/claude" -p',
            "model": "claude-opus-4-1",
            "effort": "high",
            "agent_timeout": 1,
        }
        with patch.object(agent.asyncio, "create_subprocess_exec", create_process):
            result = await collect("analyze this", config)

        self.assertEqual(result, "complete")
        self.assertEqual(captured["args"][0], "/opt/Agent CLI/claude")
        self.assertIn("--model", captured["args"])
        self.assertIn("claude-opus-4-1", captured["args"])
        self.assertIn("--effort", captured["args"])
        self.assertEqual(captured["args"][-1], "analyze this")

    async def test_nonzero_exit_without_stderr_is_an_error(self):
        process = FakeProcess(returncode=2)

        async def create_process(*args, **kwargs):
            return process

        with patch.object(agent.asyncio, "create_subprocess_exec", create_process):
            with self.assertRaisesRegex(RuntimeError, "exited with code 2"):
                await collect("prompt", {"command": "claude -p", "agent_timeout": 1})

    async def test_timeout_kills_the_agent(self):
        process = FakeProcess(returncode=None, keep_open=True)

        async def create_process(*args, **kwargs):
            return process

        with patch.object(agent.asyncio, "create_subprocess_exec", create_process):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                await collect("prompt", {"command": "claude -p", "agent_timeout": 0.01})

        self.assertTrue(process.killed)

    async def test_invalid_command_is_rejected_before_spawn(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid CLI agent command"):
            await collect("prompt", {"command": '"unterminated'})


if __name__ == "__main__":
    unittest.main()
