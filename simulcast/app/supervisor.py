from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

RUNNING = "running"
STOPPED = "stopped"
FAILED = "failed"

MAX_FAILURES = 3
SETTLE_S = 20.0
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Job:
    """Kills the children when the shell process goes away, even on a crash."""

    def __init__(self) -> None:
        self.handle = None
        self._k32 = None
        if os.name != "nt":
            return
        try:
            self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.handle = self._k32.CreateJobObjectW(None, None)
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = self._k32.SetInformationJobObject(
                self.handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
            if not ok:
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
        except Exception as exc:
            log.warning("job object unavailable: %s", exc)
            self.handle = None

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle is None or self._k32 is None:
            return
        try:
            if not self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        except Exception as exc:
            log.warning("cannot put %s in the job: %s", proc.pid, exc)


@dataclass
class EngineSpec:
    key: str
    title: str
    args: list[str]
    log_name: str


class Engine:
    def __init__(self, spec: EngineSpec, root: Path, job: _Job):
        self.spec = spec
        self.root = root
        self.job = job
        self.proc: subprocess.Popen | None = None
        self.state = STOPPED
        self.failures = 0
        self.started_at = 0.0
        self._stop_requested = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def set_args(self, args: list[str]) -> None:
        self.spec.args = args

    def start(self) -> None:
        if self.running:
            return
        log_path = self.root / "logs" / self.spec.log_name
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.root)
        env.setdefault("PYTHONUTF8", "1")
        out = open(log_path, "ab", buffering=0)
        try:
            self.proc = subprocess.Popen(
                [sys.executable, *self.spec.args],
                cwd=str(self.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        finally:
            out.close()
        self.job.add(self.proc)
        self.state = RUNNING
        self.started_at = time.monotonic()
        self._stop_requested = False
        log.info("%s started (pid %s)", self.spec.title, self.proc.pid)

    def stop(self, timeout: float = 5.0) -> None:
        proc = self.proc
        self._stop_requested = True
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    pass
        self.proc = None
        self._stop_requested = False
        self.state = STOPPED
        log.info("%s stopped", self.spec.title)

    def restart(self) -> None:
        self.stop()
        self.start()

    def tick(self) -> bool:
        proc = self.proc
        if proc is None:
            return False
        code = proc.poll()
        if code is None:
            return False
        self.proc = None
        if self._stop_requested:
            self._stop_requested = False
            self.state = STOPPED
            return True
        if time.monotonic() - self.started_at >= SETTLE_S:
            self.failures = 0
        self.failures += 1
        log.warning("%s exited with %s (%s/%s)", self.spec.title, code, self.failures, MAX_FAILURES)
        if self.failures >= MAX_FAILURES:
            self.state = FAILED
            return True
        self.start()
        return True


class Supervisor:
    def __init__(self, root: Path, specs: list[EngineSpec]):
        self.root = root
        self.job = _Job()
        self.engines: dict[str, Engine] = {spec.key: Engine(spec, root, self.job) for spec in specs}

    def __getitem__(self, key: str) -> Engine:
        return self.engines[key]

    def start_all(self) -> None:
        for engine in self.engines.values():
            engine.start()

    def stop_all(self) -> None:
        for engine in self.engines.values():
            engine.stop()

    def tick(self) -> bool:
        changed = False
        for engine in self.engines.values():
            if engine.tick():
                changed = True
        return changed
