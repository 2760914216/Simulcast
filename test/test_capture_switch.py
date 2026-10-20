import simulcast.audio.capture as capture
from simulcast.cli import _apply_control


class FakeMic:
    def __init__(self, name):
        self.name = name


def patch(default_info):
    capture.default_loopback = lambda: default_info()
    capture.default_microphone = lambda: default_info()
    capture._get = lambda device_id: FakeMic(f"mic:{device_id}")


def check_follow():
    state = {"info": {"id": "speaker-a", "name": "Speaker A"}}
    patch(lambda: state["info"])
    cap = capture.LiveCapture(loopback=True)
    assert cap._desired() == ("speaker-a", "Speaker A")
    assert cap._should_switch("speaker-a") is False
    state["info"] = {"id": "speaker-b", "name": "Speaker B"}
    assert cap._should_switch("speaker-a") is True
    assert cap._desired() == ("speaker-b", "Speaker B")
    print("follow default           ok")


def check_manual():
    patch(lambda: {"id": "speaker-a", "name": "Speaker A"})
    cap = capture.LiveCapture(loopback=True)
    cap.set_device("headset")
    assert cap._desired() == ("headset", "mic:headset")
    assert cap._should_switch("headset") is False
    assert cap._should_switch("speaker-a") is True
    cap.set_device(None)
    assert cap._desired() == ("speaker-a", "Speaker A")
    assert cap._should_switch("speaker-a") is False
    print("manual pin overrides     ok")


def check_missing():
    patch(lambda: None)
    cap = capture.LiveCapture(loopback=True)
    try:
        cap._desired()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert cap._should_switch("speaker-a") is True
    print("no default device        ok")


def check_control():
    class FakeCap:
        def __init__(self):
            self.calls = []

        def set_device(self, device):
            self.calls.append(device)

    loop, mic = FakeCap(), FakeCap()
    caps = {"loopback": loop, "mic": mic}
    _apply_control(caps, {"type": "control", "action": "capture.device", "kind": "loopback", "device": "d1"})
    _apply_control(caps, {"type": "control", "action": "capture.device", "kind": "loopback", "device": None})
    _apply_control(caps, {"type": "control", "action": "capture.device", "kind": "mic", "device": "m1"})
    _apply_control(caps, {"type": "control", "action": "capture.device", "kind": "both", "device": "x"})
    _apply_control(caps, {"type": "caption"})
    assert loop.calls == ["d1", None], loop.calls
    assert mic.calls == ["m1"], mic.calls
    print("control dispatch         ok")


check_follow()
check_manual()
check_missing()
check_control()
