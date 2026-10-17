import pathlib
import tempfile

from simulcast.config import ConfigError, load

BASE = """
[app]
profile = "local"

[asr]
provider = "a"

[asr.providers.a]
base_url = "http://asr"
model = "asr-model"
max_tokens = 111

[mt]
provider = "m1"
target_lang = "中文"

[mt.providers.m1]
base_url = "http://mt1"
model = "mt-one"

[mt.providers.m2]
base_url = "http://mt2"
model = "mt-two"
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_profile_override():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root / "default.toml", BASE)
        write(root / "profiles" / "local.toml", '[mt]\nprovider = "m2"\n')
        cfg = load(root / "default.toml")
        assert cfg.profile == "local"
        assert cfg.mt.active.model == "mt-two"
        assert cfg.asr.active.max_tokens == 111

        base_only = load(root / "default.toml", profile="missing")
        assert base_only.mt.active.model == "mt-one"


def test_unknown_provider():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root / "default.toml", BASE.replace('provider = "a"', 'provider = "nope"'))
        try:
            load(root / "default.toml")
        except ConfigError as exc:
            assert "nope" in str(exc)
        else:
            raise AssertionError("expected ConfigError")


def test_missing_base_url():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root / "default.toml", BASE.replace('base_url = "http://asr"', 'base_url = ""'))
        try:
            load(root / "default.toml")
        except ConfigError as exc:
            assert "base_url" in str(exc)
        else:
            raise AssertionError("expected ConfigError")


def test_repo_configs():
    cfg = load()
    assert cfg.profile == "local"
    assert cfg.asr.active.base_url.endswith(":8091")
    assert cfg.mt.active.base_url.endswith(":8092")

    cloud = load(profile="cloud-mt")
    assert cloud.mt.active.model == "deepseek-chat"
    assert cloud.mt.active.api_key_env == "DEEPSEEK_API_KEY"
    assert cloud.asr.active.model == cfg.asr.active.model


def test_render_config():
    import tomllib

    from simulcast.render import theme

    cfg = load()
    assert cfg.mt.active.max_tokens == 256

    path = pathlib.Path(__file__).resolve().parent.parent / "config" / "render.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    theme.apply(data.get("theme", {}))
    assert theme.TEXT_TAIL == data["theme"]["text_tail"]
    assert theme.RADIUS == data["theme"]["radius"]
    assert data["window"]["history_lines"] == 1


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        fn()
        print("ok", name)
print("OK")
