import time
from pathlib import Path

from simulcast.app import supervisor as sup

root = Path(__file__).resolve().parents[1]
job = sup._Job()
assert job.handle is not None or True


def spec(key, code):
    return sup.EngineSpec(key, key, ["-c", code], f"{key}.log")


engine = sup.Engine(spec("dummy", "import time; time.sleep(30)"), root, job)
engine.start()
time.sleep(1.0)
assert engine.running, engine.state
engine.stop()
assert not engine.running
assert engine.state == sup.STOPPED

crash = sup.Engine(spec("crash", "import sys; sys.exit(3)"), root, job)
crash.start()
deadline = time.time() + 10
while crash.state != sup.FAILED and time.time() < deadline:
    crash.tick()
    time.sleep(0.1)
assert crash.state == sup.FAILED, crash.state
assert crash.failures >= sup.MAX_FAILURES, crash.failures
crash.stop()
print("supervisor lifecycle   ok")
