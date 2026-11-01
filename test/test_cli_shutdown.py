"""CLI errors/cancellation close pending work before closing its output sink."""
import asyncio
import unittest
from unittest.mock import patch

import numpy as np

from simulcast import cli
from simulcast.asr.base import AsrResult
from simulcast.config import AppCfg
from simulcast.pipeline.orchestrator import Orchestrator


class CliShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_failure_and_cancellation_cleanup(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                decoded = asyncio.Event()
                instances = []
                events = []

                class Asr:
                    async def healthy(self):
                        return True

                    async def transcribe(self, pcm):
                        decoded.set()
                        return AsrResult("A pending caption.", "English", 1)

                class Sink:
                    closed = False

                    async def emit(self, message):
                        if self.closed:
                            raise AssertionError("output emitted after sink.close")
                        events.append(message)

                    async def close(self):
                        self.closed = True

                async def frames(*args):
                    for index in range(60):
                        yield (index + 1) * .02, np.full(320, .1, dtype=np.float32)
                        await asyncio.sleep(0)
                    await asyncio.wait_for(decoded.wait(), 1)
                    if cancel:
                        raise asyncio.CancelledError()
                    raise RuntimeError("input failed")

                def create(*args, **kwargs):
                    orch = Orchestrator(*args, **kwargs)
                    instances.append(orch)
                    self.addAsyncCleanup(orch.stop)
                    return orch

                sink = Sink()
                args = cli._parser().parse_args(["run", "--input", "unused.wav", "--quiet", "--no-mt"])
                with patch.object(cli, "load", return_value=AppCfg()), \
                     patch.object(cli, "build_asr", return_value=Asr()), \
                     patch.object(cli, "MultiSink", return_value=sink), \
                     patch.object(cli, "file_frames", frames), \
                     patch.object(cli, "Orchestrator", create):
                    with self.assertRaises(asyncio.CancelledError if cancel else RuntimeError):
                        await cli._cmd_run(args)
                orch = instances[0]
                self.assertTrue(sink.closed)
                self.assertFalse(orch._workers, "CLI left its translation worker running")
                self.assertEqual(orch._utterances.flush(), [])
                self.assertIn("stop_unconfirmed", [e.get("reason") for e in events])


if __name__ == "__main__":
    unittest.main()
