"""带宽测速（curl 调用与结果解析）测试。"""
import pytest

from fakes import FakeTime, socket_factory  # noqa: F401  （保持与其它测试一致的导入风格）


class CurlResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_curl(module, monkeypatch):
    calls = []
    state = {"result": CurlResult(), "raise": None}

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        if state["raise"]:
            raise state["raise"]
        return state["result"]

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return {"calls": calls, "state": state}


def test_bandwidth_speed_math(module, fake_curl):
    fake_curl["state"]["result"] = CurlResult(stdout="1000000 1.0")
    node, speed = module.measure_bandwidth_curl("1.2.3.4:443#US")
    assert node == "1.2.3.4:443#US"
    assert speed == pytest.approx(8.0)


def test_bandwidth_uses_resolve_and_target_url(module, fake_curl):
    fake_curl["state"]["result"] = CurlResult(stdout="500000 2.0")
    module.measure_bandwidth_curl("1.2.3.4:8443#US")
    cmd = fake_curl["calls"][0]["cmd"]
    assert "--resolve" in cmd
    assert "speed.cloudflare.com:8443:1.2.3.4" in cmd
    assert module.BANDWIDTH_URL in cmd
    assert fake_curl["calls"][0]["kwargs"]["timeout"] == module.BANDWIDTH_TIMEOUT + module.BANDWIDTH_PROCESS_BUFFER


@pytest.mark.parametrize("result", [
    CurlResult(returncode=1, stdout="1000 1.0"),
    CurlResult(returncode=0, stdout=""),
    CurlResult(returncode=0, stdout="1000"),
    CurlResult(returncode=0, stdout="abc def"),
    CurlResult(returncode=0, stdout="0 1.0"),
    CurlResult(returncode=0, stdout="1000 0"),
])
def test_bandwidth_rejects_unusable_output(module, fake_curl, result):
    fake_curl["state"]["result"] = result
    assert module.measure_bandwidth_curl("1.2.3.4:443#US")[1] == 0


def test_bandwidth_survives_curl_crash(module, fake_curl):
    fake_curl["state"]["raise"] = FileNotFoundError("curl 不存在")
    assert module.measure_bandwidth_curl("1.2.3.4:443#US")[1] == 0


def test_bandwidth_bad_node_returns_zero(module, fake_curl):
    assert module.measure_bandwidth_curl("garbage")[1] == 0
    assert fake_curl["calls"] == []


def test_bandwidth_filter_without_curl(module, monkeypatch, capsys):
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    assert module.bandwidth_filter(["1.2.3.4:443#US"]) == []
    assert "未检测到 curl" in capsys.readouterr().out


def test_bandwidth_filter_empty_candidates(module):
    assert module.bandwidth_filter([]) == []


def test_bandwidth_filter_sorts_and_drops_zero(module, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)
    speeds = {"1.2.3.4:443#US": 5.0, "5.6.7.8:443#DE": 0, "9.9.9.9:443#JP": 20.0}
    monkeypatch.setattr(module, "measure_bandwidth_curl", lambda node: (node, speeds[node]))

    results = module.bandwidth_filter(list(speeds))
    assert results == [("9.9.9.9:443#JP", 20.0), ("1.2.3.4:443#US", 5.0)]


def test_bandwidth_filter_preserves_all_when_nothing_measured(module, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)
    monkeypatch.setattr(module, "measure_bandwidth_curl", lambda node: (node, 0))
    assert module.bandwidth_filter(["1.2.3.4:443#US"]) == []
