"""Run repository script tests; each script gets a fresh Python process."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="*", help="test script basenames, e.g. test_context")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.tests:
        files = []
        for name in args.tests:
            if Path(name).name != name or not name.startswith("test_"):
                parser.error("pass test basenames only")
            path = root / "test" / (name if name.endswith(".py") else name + ".py")
            if not path.is_file():
                parser.error(f"missing test: {path.name}")
            files.append(path)
    else:
        tracked = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "test/test_*.py"], cwd=root, text=True)
        files = sorted({root / name for name in tracked.splitlines()})
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
    counts = dict(PASS=0, SKIP=0, FAIL=0)
    started = time.monotonic()
    for path in files:
        try:
            result = subprocess.run([sys.executable, "-B", str(path)], cwd=root, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    encoding="utf-8", errors="replace", timeout=60)
            status = "FAIL" if result.returncode else ("SKIP" if "SKIP:" in result.stdout else "PASS")
            counts[status] += 1
            print(f"{status} {path.name}", flush=True)
            if status != "PASS":
                print(result.stdout[-8000:], flush=True)
        except subprocess.TimeoutExpired:
            counts["FAIL"] += 1
            print(f"FAIL {path.name}: timed out after 60 s", flush=True)
    print(f"SUMMARY {counts}; {time.monotonic() - started:.1f} s", flush=True)
    return int(bool(counts["FAIL"]))


if __name__ == "__main__":
    raise SystemExit(main())
