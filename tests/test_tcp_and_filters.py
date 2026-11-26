"""TCP 筛选、可用性二次筛选、HTTP 检测的离线测试。"""
import pytest

from fakes import FakeRequests, FakeResponse, FakeTime, socket_factory

AVAILABILITY_API = "https://api.090227.xyz/check"


@pytest.fixture
def fake_time(module, monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(module, "time", fake)
    return fake


@pytest.fixture
def fake_sockets(module, monkeypatch, plan=None):
    record = []

    def _install(mapping, default=None):
        monkeypatch.setattr(module.socket, "socket", socket_factory(mapping, record, default))
        return record

    return _install


# ---------------------------------------------------------------- test_tcp_latency
def test_tcp_latency_measures_min_of_probes(module, monkeypatch, fake_sockets):
    record = fake_sockets({("1.2.3.4", 443): "ok"})
    fake = FakeTime(values=[0.0, 0.5, 0.0, 0.2])
    monkeypatch.setattr(module, "time", fake)

    latency, success = module.test_tcp_latency("1.2.3.4", 443, timeout=1.0, probes=2)
    assert success == 2
    assert latency == pytest.approx(0.2)
    assert record == [("1.2.3.4", 443), ("1.2.3.4", 443)]


def test_tcp_latency_counts_only_successful_probes(module, monkeypatch, fake_sockets):
    fake_sockets({("1.2.3.4", 443): ConnectionRefusedError("no")})
    monkeypatch.setattr(module, "time", FakeTime(values=[0.0, 0.1]))

    latency, success = module.test_tcp_latency("1.2.3.4", 443, timeout=1.0, probes=3)
    assert success == 0
    assert latency == float("inf")


def test_tcp_latency_survives_partial_failure(module, monkeypatch, fake_sockets):
    calls = {"n": 0}

    def plan_factory(family, kind):
        from fakes import FakeSocket

        class Flaky(FakeSocket):
            def connect(self, address):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise TimeoutError("second probe fails")
                return super().connect(address)

        return Flaky(family, kind, {("1.2.3.4", 443): "ok"}, [])

    monkeypatch.setattr(module.socket, "socket", plan_factory)
    # 注意：失败的探测只消耗一个时间戳（拿到开始时间后就抛异常）
    monkeypatch.setattr(module, "time", FakeTime(values=[0.0, 0.5, 0.0, 0.0, 0.4]))

    latency, success = module.test_tcp_latency("1.2.3.4", 443, timeout=1.0, probes=3)
    assert success == 2
    assert latency == pytest.approx(0.4)


def test_tcp_latency_sets_socket_timeout(module, monkeypatch, fake_sockets):
    from fakes import FakeSocket

    created = []

    def factory(family, kind):
        sock = FakeSocket(family, kind, {("1.2.3.4", 443): "ok"}, [])
        created.append(sock)
        return sock

    monkeypatch.setattr(module.socket, "socket", factory)
    monkeypatch.setattr(module, "time", FakeTime())
    module.test_tcp_latency("1.2.3.4", 443, timeout=2.5, probes=1)
    assert created[0].timeout == 2.5


# ---------------------------------------------------------------- test_node
def test_test_node_ignores_malformed_strings(module):
    assert module.test_node("not-a-node") is None
    assert module.test_node("1.2.3.4#US") is None
    assert module.test_node("[::1]:443#US") is None


def test_test_node_returns_latency_and_country(module, monkeypatch, fake_sockets):
    fake_sockets({("1.2.3.4", 443): "ok"})
    monkeypatch.setattr(module, "time", FakeTime(values=[0.0, 0.25]))
    monkeypatch.setattr(module, "TCP_PROBES", 1)
    monkeypatch.setattr(module, "MIN_SUCCESS_RATE", 1.0)

    node, latency, country, success = module.test_node("1.2.3.4:443#US")
    assert node == "1.2.3.4:443#US"
    assert country == "US"
    assert success == 1
    assert latency == pytest.approx(0.25)


def test_test_node_rejects_low_success_rate(module, monkeypatch, fake_sockets):
    fake_sockets({("1.2.3.4", 443): "ok"})
    monkeypatch.setattr(module, "time", FakeTime())
    monkeypatch.setattr(module, "TCP_PROBES", 4)
    monkeypatch.setattr(module, "MIN_SUCCESS_RATE", 1.0)
    # 4 次探测里只有 1 次成功（其余端口连不上）
    monkeypatch.setattr(module, "test_tcp_latency", lambda ip, port, **kwargs: (0.01, 1))
    assert module.test_node("1.2.3.4:443#US") is None


def test_test_node_accepts_min_success_rate(module, monkeypatch):
    monkeypatch.setattr(module, "TCP_PROBES", 4)
    monkeypatch.setattr(module, "MIN_SUCCESS_RATE", 0.5)
    monkeypatch.setattr(module, "test_tcp_latency", lambda ip, port, **kwargs: (0.01, 2))
    assert module.test_node("1.2.3.4:443#US") is not None


# ---------------------------------------------------------------- check_availability
@pytest.fixture
def fake_requests(module, monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(module, "requests", fake)
    return fake


def test_check_availability_success(fake_requests, module):
    fake_requests.route(AVAILABILITY_API, FakeResponse(json_data={
        "success": True,
        "inferred_stack": "ipv4",
        "probe_results": {"ipv4": {"exit": {"country": "US"}}},
    }))
    node, ok, stack, exit_info = module.check_availability("1.2.3.4:443#US")
    assert (node, ok, stack) == ("1.2.3.4:443#US", True, "ipv4")
    assert exit_info == {"country": "US"}
    assert fake_requests.calls_to(AVAILABILITY_API)[0]["kwargs"]["params"] == {"proxyip": "1.2.3.4:443"}


def test_check_availability_reports_failure(fake_requests, module):
    fake_requests.route(AVAILABILITY_API, FakeResponse(json_data={"success": False}))
    assert module.check_availability("1.2.3.4:443#US")[1] is False


def test_check_availability_network_error(fake_requests, module):
    fake_requests.route(AVAILABILITY_API, module.requests.exceptions.ConnectTimeout("x"))
    assert module.check_availability("1.2.3.4:443#US")[1] is False


def test_check_availability_bad_node(fake_requests, module):
    assert module.check_availability("garbage") == ("garbage", False, "unknown", {})


def test_check_availability_prefers_ipv6_probe(fake_requests, module):
    fake_requests.route(AVAILABILITY_API, FakeResponse(json_data={
        "success": True,
        "inferred_stack": "dual",
        "probe_results": {"ipv6": {"exit": {"country": "DE"}}, "ipv4": {"exit": {"country": "US"}}},
    }))
    assert module.check_availability("1.2.3.4:443#US")[3] == {"country": "DE"}


# ---------------------------------------------------------------- check_http_server
def test_http_check_accepts_cloudflare_node(fake_requests, module):
    fake_requests.route("cdn-cgi/trace", FakeResponse(status_code=400, headers={"server": "cloudflare"}))
    node, ok, detail = module.check_http_server("1.2.3.4:443#US", 3, 0, 0, "HEAD")
    assert (node, ok) == ("1.2.3.4:443#US", True)
    assert detail == "cloudflare"
    assert fake_requests.calls_to("cdn-cgi/trace")[0]["method"] == "head"


def test_http_check_rejects_non_cloudflare_server(fake_requests, module):
    fake_requests.route("cdn-cgi/trace", FakeResponse(status_code=400, headers={"server": "nginx"}))
    assert module.check_http_server("1.2.3.4:443#US", 3, 0, 0, "HEAD") == ("1.2.3.4:443#US", False, "nginx")


def test_http_check_rejects_unexpected_status(fake_requests, module):
    fake_requests.route("cdn-cgi/trace", FakeResponse(status_code=200, headers={"server": "cloudflare"}))
    assert module.check_http_server("1.2.3.4:443#US", 3, 0, 0, "HEAD")[2] == "status_200"


def test_http_check_get_method_uses_get(fake_requests, module):
    fake_requests.route("cdn-cgi/trace", FakeResponse(status_code=400, headers={"server": "cloudflare"}))
    module.check_http_server("1.2.3.4:443#US", 3, 0, 0, "GET")
    assert fake_requests.calls_to("cdn-cgi/trace")[0]["method"] == "get"


def test_http_check_retries_timeout_then_gives_up(fake_requests, module):
    attempts = []

    def responder(call):
        attempts.append(call)
        return module.requests.exceptions.Timeout("slow")

    fake_requests.route("cdn-cgi/trace", responder)
    fake = FakeTime()
    module.time = fake
    assert module.check_http_server("1.2.3.4:443#US", 3, 2, 0.5, "HEAD") == ("1.2.3.4:443#US", False, "timeout")
    assert len(attempts) == 3
    assert fake.sleeps == [0.5, 0.5]


def test_http_check_retries_timeout_then_succeeds(fake_requests, module, monkeypatch):
    state = {"count": 0}

    def responder(call):
        state["count"] += 1
        if state["count"] == 1:
            return module.requests.exceptions.Timeout("slow")
        return FakeResponse(status_code=400, headers={"server": "cloudflare"})

    fake_requests.route("cdn-cgi/trace", responder)
    monkeypatch.setattr(module, "time", FakeTime())
    assert module.check_http_server("1.2.3.4:443#US", 3, 1, 0.1, "HEAD")[1] is True


def test_http_check_connection_error_is_not_retried(fake_requests, module):
    attempts = []

    def responder(call):
        attempts.append(call)
        return module.requests.exceptions.ConnectionError("refused")

    fake_requests.route("cdn-cgi/trace", responder)
    assert module.check_http_server("1.2.3.4:443#US", 3, 3, 0, "HEAD")[2] == "connection_error"
    assert len(attempts) == 1


def test_http_check_bad_node(fake_requests, module):
    assert module.check_http_server("garbage", 3, 0, 0, "HEAD")[2] == "parse_error"


# ---------------------------------------------------------------- availability_filter_candidates
def test_availability_filter_disabled_returns_input(module, monkeypatch):
    monkeypatch.setattr(module, "TEST_AVAILABILITY", False)
    candidates = ["1.2.3.4:443#US"]
    assert module.availability_filter_candidates(candidates) == (candidates, {}, {})


def test_availability_filter_empty_input(module, monkeypatch):
    monkeypatch.setattr(module, "TEST_AVAILABILITY", True)
    assert module.availability_filter_candidates([]) == ([], {}, {})


def test_availability_filter_keeps_only_ok_nodes(module, monkeypatch):
    monkeypatch.setattr(module, "TEST_AVAILABILITY", True)
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)

    def fake_check(node):
        if node.endswith("#US"):
            return (node, True, "ipv4", {"country": "US"})
        return (node, False, "unknown", {})

    monkeypatch.setattr(module, "check_availability", fake_check)
    passed, ip_info, exit_details = module.availability_filter_candidates(["1.2.3.4:443#US", "5.6.7.8:443#DE"])
    assert passed == ["1.2.3.4:443#US"]
    assert ip_info == {"1.2.3.4:443#US": "ipv4"}
    assert exit_details == {"1.2.3.4:443#US": {"country": "US"}}


def test_availability_retry_recovers(module, monkeypatch):
    rounds = {"n": 0}

    def fake_round(candidates):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return [], {}, {}
        return candidates, {"ipv4": "ipv4"}, {}

    monkeypatch.setattr(module, "TEST_AVAILABILITY", True)
    monkeypatch.setattr(module, "AVAILABILITY_RETRY_MAX", 2)
    monkeypatch.setattr(module, "availability_filter_candidates", fake_round)
    fake = FakeTime()
    monkeypatch.setattr(module, "time", fake)

    passed, ip_info, _ = module.availability_filter_with_retry(["1.2.3.4:443#US"])
    assert passed == ["1.2.3.4:443#US"]
    assert fake.sleeps == [module.AVAILABILITY_RETRY_DELAY]


def test_availability_retry_failure_degrades_and_notifies(module, monkeypatch):
    notified = []
    monkeypatch.setattr(module, "TEST_AVAILABILITY", True)
    monkeypatch.setattr(module, "AVAILABILITY_RETRY_MAX", 2)
    monkeypatch.setattr(module, "availability_filter_candidates", lambda candidates: ([], {}, {}))
    monkeypatch.setattr(module, "send_wxpusher_notification", lambda **kwargs: notified.append(kwargs))
    monkeypatch.setattr(module, "time", FakeTime())

    candidates = ["1.2.3.4:443#US"]
    assert module.availability_filter_with_retry(candidates) == (candidates, {}, {})
    assert len(notified) == 1


# ---------------------------------------------------------------- http_server_filter
def test_http_filter_disabled_returns_input(module, monkeypatch):
    monkeypatch.setattr(module, "HTTP_TEST_ENABLED", False)
    candidates = ["1.2.3.4:443#US"]
    assert module.http_server_filter(candidates, {"HTTP_TEST_ENABLED": False}) == candidates


def test_http_filter_keeps_valid_nodes(module, monkeypatch):
    monkeypatch.setattr(module, "HTTP_TEST_MAX_ROUNDS", 1)
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)
    monkeypatch.setattr(module, "check_http_server", lambda node, *a: (node, node.endswith("US"), "cloudflare"))

    passed = module.http_server_filter(["1.2.3.4:443#US", "5.6.7.8:443#DE"], {"HTTP_TEST_ENABLED": True})
    assert passed == ["1.2.3.4:443#US"]


def test_http_filter_degrades_when_all_fail(module, monkeypatch):
    notified = []
    monkeypatch.setattr(module, "HTTP_TEST_MAX_ROUNDS", 2)
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)
    monkeypatch.setattr(module, "check_http_server", lambda node, *a: (node, False, "timeout"))
    monkeypatch.setattr(module, "send_wxpusher_notification", lambda **kwargs: notified.append(kwargs))
    monkeypatch.setattr(module, "time", FakeTime())

    candidates = ["1.2.3.4:443#US"]
    assert module.http_server_filter(candidates, {"HTTP_TEST_ENABLED": True}) == candidates
    assert len(notified) == 1
