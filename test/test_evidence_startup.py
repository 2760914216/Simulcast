"""Optional observation startup errors are reported as session errors."""
import asyncio
from unittest.mock import patch

from simulcast import cli
from simulcast.config import AppCfg, ConfigError
from simulcast.pipeline.orchestrator import Orchestrator


class Sink:
    def __init__(self):
        self.events, self.closed = [], False

    async def emit(self, message):
        self.events.append(message)

    async def close(self):
        self.closed = True


class MissingRuntime:
    def __init__(self, path):
        pass

    def measure(self, pcm, sample_rate):
        raise RuntimeError("Speech evidence requires optional onnxruntime")


async def main():
    cfg = AppCfg()
    cfg.asr.speech_evidence_mode = "observe"
    cfg.asr.speech_evidence_model = "missing-model.onnx"
    sink = Sink()
    try:
        Orchestrator(cfg, None, None, sink)
    except ConfigError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("missing model accepted")

    with patch("simulcast.audio.speech_evidence.SileroEvidence", MissingRuntime):
        orch = Orchestrator(cfg, None, None, sink)
        try:
            await orch.start()
        except ConfigError as exc:
            assert "onnxruntime" in str(exc)
            assert not orch._workers
        else:
            raise AssertionError("missing runtime accepted")

        args = cli._parser().parse_args(["run", "--quiet", "--no-mt"])
        with patch.object(cli, "load", return_value=cfg), \
             patch.object(cli, "build_asr", return_value=None), \
             patch.object(cli, "MultiSink", return_value=sink):
            code = await cli._cmd_run(args)
        assert code == 2
        assert sink.closed
        assert sink.events[-1]["state"] == "error", sink.events
        assert "onnxruntime" in sink.events[-1]["detail"]
    print("optional observation startup failures reported cleanly")


asyncio.run(main())
