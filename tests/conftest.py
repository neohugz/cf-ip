"""测试夹具：隔离配置、模块全局快照恢复、以及 requests / socket / subprocess 的替身。

测试不打网络、不跑 curl、不动 Cloudflare：所有外部依赖都以替身注入。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试用配置：与仓库里的 config.json 完全隔离，避免读取到真实令牌
TEST_CONFIG = {
    "USE_GLOBAL_MODE": True,
    "GLOBAL_TOP_N": 2,
    "PER_COUNTRY_TOP_N": 1,
    "BANDWIDTH_CANDIDATES": 4,
    "TCP_PROBES": 1,
    "TEST_AVAILABILITY": False,
    "HTTP_TEST_ENABLED": False,
    "CF_ENABLED": False,
    "ENABLE_WXPUSHER": False,
    "ENABLE_LOGGING": False,
    "DNS_RECORD_TYPE": "TXT",
    "OUTPUT_FILE": "ip.txt",
    "ADDITIONAL_SOURCES": [{"url": "https://example.invalid/all.txt", "enabled": True}],
}

_TMPDIR = Path(tempfile.mkdtemp(prefix="cfnb-tests-"))
CONFIG_PATH = _TMPDIR / "config.json"
CONFIG_PATH.write_text(json.dumps(TEST_CONFIG, ensure_ascii=False), encoding="utf-8")
os.environ["CFNB_CONFIG"] = str(CONFIG_PATH)

import main as cfnb  # noqa: E402  （必须在设置 CFNB_CONFIG 之后导入）


def _global_keys():
    return [key for key in vars(cfnb) if key.isupper()]


@pytest.fixture(autouse=True)
def isolated_globals():
    """每个测试结束后恢复模块全局变量与关键函数，避免测试互相污染"""
    snapshot = {key: getattr(cfnb, key) for key in _global_keys()}
    functions = {name: getattr(cfnb, name) for name in dir(cfnb) if not name.startswith("__")}
    yield
    for key, value in snapshot.items():
        setattr(cfnb, key, value)
    for name, value in functions.items():
        setattr(cfnb, name, value)


@pytest.fixture
def module():
    return cfnb


@pytest.fixture
def configure():
    """按需覆盖模块全局配置，例如 configure(GLOBAL_TOP_N=1)"""
    def _configure(**overrides):
        for key, value in overrides.items():
            setattr(cfnb, key, value)
    return _configure


@pytest.fixture
def config_file(tmp_path):
    """写出一个临时配置文件，返回 (路径, 写入函数)"""
    def _write(payload, name="config.json"):
        target = tmp_path / name
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return target
    return _write
