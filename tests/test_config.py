"""配置解析、校验与路径解析的测试。"""
import json

import pytest

MINIMAL = {"ADDITIONAL_SOURCES": [{"url": "https://example.invalid/all.txt", "enabled": True}]}


def test_load_config_fills_missing_keys(config_file, module):
    path = config_file(MINIMAL)
    config = module.load_config(str(path))
    for key, value in module.DEFAULT_CONFIG.items():
        assert key in config
        if key in MINIMAL:
            continue
        assert config[key] == value
    assert config["ADDITIONAL_SOURCES"] == MINIMAL["ADDITIONAL_SOURCES"]


def test_load_config_keeps_explicit_values(config_file, module):
    path = config_file({**MINIMAL, "GLOBAL_TOP_N": 7, "USE_GLOBAL_MODE": False})
    config = module.load_config(str(path))
    assert config["GLOBAL_TOP_N"] == 7
    assert config["USE_GLOBAL_MODE"] is False


def test_load_config_missing_file_exits(tmp_path, module):
    with pytest.raises(SystemExit) as excinfo:
        module.load_config(str(tmp_path / "nope.json"))
    assert excinfo.value.code == 1


def test_load_config_malformed_json_exits(tmp_path, module):
    broken = tmp_path / "config.json"
    broken.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        module.load_config(str(broken))
    assert excinfo.value.code == 1


def test_load_config_array_root_exits(tmp_path, module):
    array = tmp_path / "config.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        module.load_config(str(array))
    assert excinfo.value.code == 1


def test_validate_config_coerces_numeric_strings(module):
    config = dict(module.DEFAULT_CONFIG)
    config["TCP_PROBES"] = "3"
    config["MIN_SUCCESS_RATE"] = "0.5"
    module.validate_config(config)
    assert config["TCP_PROBES"] == 3
    assert isinstance(config["TCP_PROBES"], int)
    assert config["MIN_SUCCESS_RATE"] == 0.5


@pytest.mark.parametrize("key,value", [
    ("MIN_SUCCESS_RATE", "abc"),
    ("MIN_SUCCESS_RATE", None),
    ("MIN_SUCCESS_RATE", True),
    ("GLOBAL_TOP_N", "3.5.1"),
])
def test_validate_config_rejects_non_numeric(module, key, value):
    config = dict(module.DEFAULT_CONFIG)
    config[key] = value
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert key in str(excinfo.value)


@pytest.mark.parametrize("key,value", [
    ("MIN_SUCCESS_RATE", 1.5),
    ("MIN_SUCCESS_RATE", -0.1),
    ("GLOBAL_TOP_N", 0),
    ("TCP_PROBES", 0),
    ("BANDWIDTH_CANDIDATES", 0),
    ("MAX_WORKERS", 0),
])
def test_validate_config_rejects_out_of_range(module, key, value):
    config = dict(module.DEFAULT_CONFIG)
    config[key] = value
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert key in str(excinfo.value)


def test_validate_config_allows_rate_of_one(module):
    config = dict(module.DEFAULT_CONFIG)
    config["MIN_SUCCESS_RATE"] = 1.0
    module.validate_config(config)
    assert config["MIN_SUCCESS_RATE"] == 1.0


@pytest.mark.parametrize("key", ["USE_GLOBAL_MODE", "ENABLE_WXPUSHER", "HTTP_TEST_ENABLED"])
def test_validate_config_rejects_string_booleans(module, key):
    config = dict(module.DEFAULT_CONFIG)
    config[key] = "true"
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert key in str(excinfo.value)


def test_validate_config_rejects_string_for_list(module):
    config = dict(module.DEFAULT_CONFIG)
    config["BLOCKED_COUNTRIES"] = "US"
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert "BLOCKED_COUNTRIES" in str(excinfo.value)


def test_validate_config_rejects_non_string_path(module):
    config = dict(module.DEFAULT_CONFIG)
    config["OUTPUT_FILE"] = 5
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert "OUTPUT_FILE" in str(excinfo.value)


def test_validate_config_rejects_source_without_url(module):
    config = dict(module.DEFAULT_CONFIG)
    config["ADDITIONAL_SOURCES"] = [{"enabled": True}]
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert "ADDITIONAL_SOURCES" in str(excinfo.value)


def test_validate_config_rejects_non_object_source(module):
    config = dict(module.DEFAULT_CONFIG)
    config["ADDITIONAL_SOURCES"] = ["https://example.invalid/all.txt"]
    with pytest.raises(ValueError) as excinfo:
        module.validate_config(config)
    assert "ADDITIONAL_SOURCES" in str(excinfo.value)


def test_validate_config_hints_placeholder_token(module, capsys):
    config = dict(module.DEFAULT_CONFIG)
    config["ENABLE_WXPUSHER"] = True
    module.validate_config(config)
    assert "占位符" in capsys.readouterr().out


def test_resolve_config_path_prefers_explicit_argument(tmp_path, module):
    explicit = tmp_path / "custom.json"
    explicit.write_text("{}", encoding="utf-8")
    assert module.resolve_config_path(str(explicit)) == str(explicit)


def test_resolve_config_path_uses_env_var(tmp_path, module, monkeypatch):
    env_config = tmp_path / "from-env.json"
    env_config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(module.CONFIG_ENV_VAR, str(env_config))
    assert module.resolve_config_path() == str(env_config)


def test_resolve_config_path_falls_back_to_cwd(tmp_path, module, monkeypatch):
    monkeypatch.delenv(module.CONFIG_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    # 用一个脚本目录里不存在的名字，才能走到「当前目录」这一层
    monkeypatch.setattr(module, "DEFAULT_CONFIG_NAME", "cfnb-test-config.json")
    cwd_config = tmp_path / "cfnb-test-config.json"
    cwd_config.write_text("{}", encoding="utf-8")
    assert module.resolve_config_path() == str(cwd_config)


def test_resolve_config_path_defaults_next_to_script(tmp_path, module, monkeypatch):
    monkeypatch.delenv(module.CONFIG_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "DEFAULT_CONFIG_NAME", "cfnb-absent-config.json")
    from pathlib import Path
    assert module.resolve_config_path() == str(Path(module.SCRIPT_DIR) / "cfnb-absent-config.json")


def test_resolve_output_path_relative_uses_base_dir(module):
    assert module.resolve_output_path("ip.txt", "/srv/cfb") == "/srv/cfb/ip.txt"
    assert module.resolve_output_path("./out/ip.txt", "/srv/cfb") == "/srv/cfb/out/ip.txt"


def test_resolve_output_path_keeps_absolute(module):
    assert module.resolve_output_path("/var/log/ip.txt", "/srv/cfb") == "/var/log/ip.txt"


def test_apply_config_sets_globals(module, tmp_path):
    config = dict(module.DEFAULT_CONFIG)
    config.update({
        "OUTPUT_FILE": "result.txt",
        "LOG_FILE": "run.log",
        "PRE_FILTER_PORTS": [443, 8443],
        "PRE_FILTER_BLOCKED_COUNTRIES": ["cn", "ru"],
        "BLOCKED_COUNTRIES": ["DE"],
        "BANDWIDTH_SIZE_MB": 1.5,
    })
    path = tmp_path / "config.json"
    module.apply_config(config, str(path))
    assert module.CONFIG_FILE == str(path)
    assert module.OUTPUT_FILE == str(tmp_path / "result.txt")
    assert module.LOG_FILE == str(tmp_path / "run.log")
    assert module.PRE_FILTER_PORTS == ["443", "8443"]
    assert module.PRE_FILTER_BLOCKED_COUNTRIES == ["CN", "RU"]
    assert module.BLOCKED_COUNTRIES == ["DE"]
    assert module.BANDWIDTH_URL.endswith("bytes=1572864")


def test_apply_config_ignores_unknown_and_lowercase_keys(module, tmp_path):
    config = dict(module.DEFAULT_CONFIG)
    config["DRY_RUN"] = True
    config["something_else"] = 1
    module.apply_config(config, str(tmp_path / "config.json"))
    assert module.DRY_RUN is False
    assert not hasattr(module, "something_else")


def test_default_config_covers_repo_config_keys(module):
    from pathlib import Path
    repo_config = json.loads((Path(module.SCRIPT_DIR) / "config.json").read_text(encoding="utf-8"))
    real_keys = {key for key in repo_config if not key.startswith("_comment")}
    assert real_keys <= set(module.DEFAULT_CONFIG)
