from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .asr import build_asr
from .config import ConfigError, load
from .hub import Hub
from .mt import build_mt
from .pipeline.orchestrator import Orchestrator
from .protocol import new_session_id, session_msg
from .sink import HubSink, JsonlSink, MultiSink, TextSink
from .sources import file_frames, live_frames

log = logging.getLogger("simulcast")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="simulcast", description="offline simultaneous interpretation")
    p.add_argument("--config", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the pipeline")
    r.add_argument("--profile", default=None, help="config profile, e.g. local / cloud-mt")
    r.add_argument("--input", help="wav file to replay in realtime")
    r.add_argument("--live", choices=["loopback", "mic", "both"], help="capture system audio, microphone, or both")
    r.add_argument("--device", default=None)
    r.add_argument("--speed", type=float, default=1.0)
    r.add_argument("--max-seconds", type=float, default=None)
    r.add_argument("--out", default=None, help="append captions as jsonl")
    r.add_argument("--fresh", action="store_true", help="truncate --out instead of appending")
    r.add_argument("--ws", action="store_true", help="also serve the caption protocol on websocket")
    r.add_argument("--port", type=int, default=None)
    r.add_argument("--no-mt", action="store_true")
    r.add_argument("--quiet", action="store_true")

    d = sub.add_parser("devices", help="list capture devices")
    d.add_argument("--json", action="store_true")

    s = sub.add_parser("serve", help="serve the caption protocol only")
    s.add_argument("--port", type=int, default=None)

    v = sub.add_parser("eval", help="segmentation quality report from a jsonl log")
    v.add_argument("path")
    v.add_argument("--sid", default=None, help="session id (default: latest)")
    v.add_argument("--worst", type=int, default=3, help="show N worst bursts")

    e = sub.add_parser("export", help="fold a jsonl caption log into a transcript")
    e.add_argument("path")
    e.add_argument("--sid", default=None, help="session id (default: latest)")
    e.add_argument("--list", action="store_true", help="list sessions in the file")
    e.add_argument("--all", action="store_true", help="include drafts, not only finals")
    e.add_argument("--format", choices=["md", "srt", "jsonl", "txt"], default="md")
    e.add_argument("-o", "--out", default=None)

    return p


async def _cmd_run(args) -> int:
    cfg = load(args.config, args.profile)
    if args.port:
        cfg.hub.port = args.port

    sinks = []
    if not args.quiet:
        sinks.append(TextSink())
    if args.out:
        from pathlib import Path

        if Path(args.out).exists() and not args.fresh:
            print(f"appending to {args.out} (use --fresh to start over)", file=sys.stderr)
        sinks.append(JsonlSink(args.out, truncate=args.fresh))

    hub = None
    if args.ws:
        hub = Hub(cfg.hub)
        await hub.start()
        sinks.append(HubSink(hub))

    sink = MultiSink(*sinks)
    sid = new_session_id()
    try:
        try:
            asr = build_asr(cfg.asr)
            mt = None if args.no_mt else build_mt(cfg.mt)
        except ConfigError as exc:
            log.error("%s", exc)
            await sink.emit(session_msg("error", sid, detail=str(exc)))
            return 2
        if asr is not None and not await asr.healthy():
            detail = f"asr unreachable at {cfg.asr.active.base_url}"
            log.error("%s", detail)
            await sink.emit(session_msg("error", sid, detail=detail))
            return 2
        if mt is not None and not await mt.healthy():
            detail = f"mt unreachable at {cfg.mt.active.base_url}"
            log.error("%s", detail)
            await sink.emit(session_msg("error", sid, detail=detail))
            return 2

        orch = Orchestrator(cfg, asr, mt, sink, sid=sid)
        await orch.start()

        if args.input:
            source = "file"
            tasks = [
                asyncio.create_task(
                    orch.run(
                        file_frames(args.input, cfg.audio.frame_ms, args.speed, cfg.audio.target_rate, args.max_seconds),
                        source="file",
                    )
                )
            ]
        else:
            source = args.live or cfg.audio.source
            loopback_arg = source in ("both", "loopback", "file")
            tasks = []
            if loopback_arg:
                tasks.append(
                    asyncio.create_task(
                        orch.run(
                            live_frames(None, True, cfg.audio.frame_ms, cfg.audio.target_rate, args.max_seconds),
                            source="loopback",
                        )
                    )
                )
            if source in ("both", "mic"):
                tasks.append(
                    asyncio.create_task(
                        orch.run(
                            live_frames(
                                args.device or cfg.audio.device,
                                False,
                                cfg.audio.frame_ms,
                                cfg.audio.target_rate,
                                args.max_seconds,
                            ),
                            source="mic",
                        )
                    )
                )

        await sink.emit(session_msg("running", sid, detail=f"source={source}"))
        await asyncio.gather(*tasks)
        await orch.finish()
        await orch.stop()
        await sink.emit(session_msg("stopped", sid))
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        await sink.close()
        if hub is not None:
            await hub.stop()


async def _cmd_serve(args) -> int:
    cfg = load(args.config)
    if args.port:
        cfg.hub.port = args.port
    hub = Hub(cfg.hub)
    await hub.start()
    print(f"serving ws://{cfg.hub.host}:{cfg.hub.port}", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await hub.stop()
    return 0


def _cmd_devices(args) -> int:
    from .audio.devices import list_inputs

    items = list_inputs()
    if args.json:
        import json

        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    for item in items:
        kind = "LOOPBACK" if item["loopback"] else "MIC     "
        print(f"{kind} {item['channels']}ch  {item['name']}")
    return 0


def _fmt_ts(sec: float) -> str:
    ms = int(round(max(0.0, float(sec)) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _cmd_eval(args) -> int:
    import collections
    import json

    rows = []
    with open(args.path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    sessions: dict[str, list[dict]] = collections.OrderedDict()
    for row in rows:
        sessions.setdefault(row.get("sid") or "(no-sid)", []).append(row)
    if not sessions:
        print("no rows", file=sys.stderr)
        return 1
    sid = args.sid or list(sessions)[-1]
    items = [r for r in sessions.get(sid, []) if r.get("type") == "eval"]
    if not items:
        print(f"no eval records in {sid}", file=sys.stderr)
        return 1

    bursts = len(items)
    with_early = sum(1 for e in items if e.get("early_count"))
    early_sents = sum(e.get("early_count", 0) for e in items)
    matched = sum(e.get("matched_sents", 0) for e in items)
    committed_chars = sum(e.get("committed_chars", 0) for e in items)
    premature_chars = sum(e.get("premature_chars", 0) for e in items)
    final_sents = sum(e.get("final_count", 0) for e in items)

    print(f"session            {sid}")
    print(f"bursts             {bursts}")
    print(f"with early commit  {with_early}  ({with_early / max(1, bursts):.0%})")
    print(f"sentences          final={final_sents}  committed-early={early_sents}  exact-match={matched}"
          f"  ({matched / max(1, early_sents):.0%})")
    print(f"char premature     {premature_chars}/{committed_chars} = {premature_chars / max(1, committed_chars):.1%}")

    worst = sorted(items, key=lambda e: e.get("premature_ratio", 0), reverse=True)[: args.worst]
    for e in worst:
        if not e.get("premature_chars"):
            continue
        early = " ".join(e.get("early_sents") or [])
        final = " ".join(e.get("final_sents") or [])
        n = 0
        while n < min(len(early), len(final)) and early[n] == final[n]:
            n += 1
        print(f"\n[{e.get('id')}] ratio={e.get('premature_ratio')} t={e.get('t0')}-{e.get('t1')}")
        print(f"  diverges at char {n}")
        print(f"  early  ...{early[max(0, n - 25): n + 45]!r}")
        print(f"  final  ...{final[max(0, n - 25): n + 45]!r}")
    return 0


def _cmd_export(args) -> int:
    import collections
    import json

    rows = []
    with open(args.path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    sessions: dict[str, list[dict]] = collections.OrderedDict()
    for row in rows:
        sessions.setdefault(row.get("sid") or "(no-sid)", []).append(row)

    if args.list:
        for sid, items in sessions.items():
            caps = [r for r in items if r.get("type") == "caption"]
            start = next((r for r in items if r.get("type") == "session" and r.get("state") == "running"), {})
            finals = len({r["id"] for r in caps if r.get("state") == "final"})
            print(f"{sid}  rows={len(caps):5d}  finals={finals:4d}  {start.get('detail', '')}")
        return 0

    if not sessions:
        print("no rows", file=sys.stderr)
        return 1

    sid = args.sid or list(sessions)[-1]
    items = sessions.get(sid)
    if not items:
        print(f"session {sid} not found", file=sys.stderr)
        return 1

    last: dict[str, dict] = {}
    for row in items:
        if row.get("type") != "caption" or not row.get("id"):
            continue
        last[row["id"]] = row

    caps = [c for c in last.values() if c.get("raw") or c.get("translation")]
    if not args.all:
        caps = [c for c in caps if c.get("state") == "final"]
    caps.sort(key=lambda c: (float(c.get("t0") or 0.0), c.get("id", "")))

    if args.format == "jsonl":
        body = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in caps)
    elif args.format == "srt":
        blocks = []
        for i, c in enumerate(caps, 1):
            t0 = float(c.get("t0") or 0.0)
            t1 = float(c.get("t1") or t0 + 2.0)
            text = (c.get("translation") or c.get("raw") or "").replace("\n", " ")
            blocks.append(f"{i}\n{_fmt_ts(t0)} --> {_fmt_ts(t1)}\n{text}\n")
        body = "\n".join(blocks)
    elif args.format == "txt":
        body = "".join((c.get("translation") or c.get("raw") or "") + "\n" for c in caps)
    else:
        lines = ["| 时间 | 原文 | 译文 |", "|---|---|---|"]
        for c in caps:
            raw = (c.get("raw") or "").replace("|", "\\|").replace("\n", " ")
            tr = (c.get("translation") or "").replace("|", "\\|").replace("\n", " ")
            t0 = float(c.get("t0") or 0.0)
            t1 = c.get("t1")
            stamp = f"{t0:.2f}-{float(t1):.2f}" if t1 is not None else f"{t0:.2f}"
            lines.append(f"| {stamp} | {raw} | {tr} |")
        body = "\n".join(lines) + "\n"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"{len(caps)} captions -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    if args.cmd == "run":
        return asyncio.run(_cmd_run(args))
    if args.cmd == "serve":
        return asyncio.run(_cmd_serve(args))
    if args.cmd == "devices":
        return _cmd_devices(args)
    if args.cmd == "export":
        return _cmd_export(args)
    if args.cmd == "eval":
        return _cmd_eval(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
