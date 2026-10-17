from __future__ import annotations

from typing import Any


def _sc():
    import soundcard as sc

    return sc


def list_inputs() -> list[dict[str, Any]]:
    sc = _sc()
    out: list[dict[str, Any]] = []
    for mic in sc.all_microphones(include_loopback=True):
        loopback = bool(getattr(mic, "isloopback", False))
        out.append(
            {
                "name": mic.name,
                "id": mic.id,
                "loopback": loopback,
                "channels": int(mic.channels),
            }
        )
    return out


def default_loopback() -> dict[str, Any] | None:
    sc = _sc()
    speaker = sc.default_speaker()
    mics = sc.all_microphones(include_loopback=True)
    if speaker is not None:
        for mic in mics:
            if getattr(mic, "isloopback", False) and speaker.name.lower() == mic.name.lower():
                return {"name": mic.name, "id": mic.id, "loopback": True, "channels": int(mic.channels)}
    for mic in mics:
        if getattr(mic, "isloopback", False):
            return {"name": mic.name, "id": mic.id, "loopback": True, "channels": int(mic.channels)}
    return None


def default_microphone() -> dict[str, Any] | None:
    sc = _sc()
    mic = sc.default_microphone()
    if mic is None:
        return None
    return {"name": mic.name, "id": mic.id, "loopback": bool(getattr(mic, "isloopback", False)), "channels": int(mic.channels)}


def _get(device_id: str):
    sc = _sc()
    return sc.get_microphone(device_id, include_loopback=True)
