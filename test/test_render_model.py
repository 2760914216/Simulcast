from simulcast.render.model import CaptionStore


def cap(cid, t0=0.0, seq=1, raw="hello", translation="", state="draft", sid="s"):
    return {
        "type": "caption",
        "id": cid,
        "t0": t0,
        "t1": None,
        "raw": raw,
        "translation": translation,
        "state": state,
        "seq": seq,
        "sid": sid,
    }


def test_snapshot_picks_latest():
    store = CaptionStore()
    current = store.apply({"type": "snapshot", "captions": [cap("a", 1), cap("b", 12)]})
    assert current["id"] == "b"


def test_new_caption_wins_even_with_older_t0():
    store = CaptionStore()
    store.apply(cap("a", 10, 1))
    current = store.apply(cap("b", 6, 2))
    assert current["id"] == "b", "carried sentence starts earlier but is new content"


def test_late_update_does_not_steal_active():
    store = CaptionStore()
    store.apply(cap("a", 10, 1, raw="first"))
    store.apply(cap("b", 20, 2, raw="second"))
    current = store.apply(cap("a", 10, 9, raw="first", translation="第一", state="final"))
    assert current["id"] == "b"


def test_translation_survives_draft_growth():
    store = CaptionStore()
    store.apply(cap("a", 1, 1, raw="Hello", translation="你好"))
    current = store.apply(cap("a", 1, 2, raw="Hello everyone", translation=""))
    assert current["translation"] == "你好", current


def test_revision_keeps_translation():
    store = CaptionStore()
    store.apply(cap("a", 1, 1, raw="Hello everyone today we will", translation="大家好，今天我们将"))
    current = store.apply(cap("a", 1, 2, raw="and more", translation=""))
    assert current["translation"] == "大家好，今天我们将", current


def test_session_switch_resets():
    store = CaptionStore()
    store.apply(cap("a", 1, 1, sid="one"))
    current = store.apply(cap("b", 2, 2, sid="two"))
    assert list(store.captions) == ["b"] and current["id"] == "b"


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        fn()
        print("ok", name)
print("OK")

def test_settings_roundtrip(tmp=None):
    import os
    import tempfile
    from pathlib import Path

    from simulcast.render import qt

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["APPDATA"] = tmpdir
        qt.save_settings({"font_size": 42, "width_ratio": 0.9, "show_source": False})
        loaded = qt.load_settings()
        assert loaded["font_size"] == 42 and abs(loaded["width_ratio"] - 0.9) < 1e-6
        assert Path(tmpdir, "simulcast", "render.json").exists()
    print("ok test_settings_roundtrip")


test_settings_roundtrip()
