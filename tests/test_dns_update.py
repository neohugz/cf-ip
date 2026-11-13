"""Cloudflare DNS 更新（节点筛选 + A/TXT 批量替换）测试。

注意：节点筛选分支的条件是 `if full_bw_results and ip_info:`，
所以走「测速结果筛选」路径时必须传入非空的 ip_info（真实运行时来自可用性检测）。
"""
import pytest

from fakes import FakeRequests, FakeResponse, FakeTime

LIST_FRAGMENT = "dns_records?type="
BATCH_FRAGMENT = "dns_records/batch"


def ip_info(*nodes, stack="ipv4"):
    return {node: stack for node in nodes}


@pytest.fixture
def cf(module, monkeypatch):
    """Cloudflare API 替身 + 常用开关，返回 (fake_requests, 通知记录列表)。"""
    fake = FakeRequests()
    notified = []
    monkeypatch.setattr(module, "requests", fake)
    monkeypatch.setattr(module, "send_wxpusher_notification", lambda **kwargs: notified.append(kwargs))
    monkeypatch.setattr(module, "time", FakeTime())
    monkeypatch.setattr(module, "CF_ENABLED", True)
    monkeypatch.setattr(module, "CF_API_TOKEN", "token")
    monkeypatch.setattr(module, "CF_ZONE_ID", "zone")
    monkeypatch.setattr(module, "CF_DNS_RECORD_NAME", "edge.example.com")
    monkeypatch.setattr(module, "DNS_UPDATE_RETRY_DELAY", 0)
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)
    fake.route(BATCH_FRAGMENT, FakeResponse(json_data={"success": True, "result": {}}))
    fake.route(LIST_FRAGMENT, FakeResponse(json_data={"success": True, "result": [{"id": "rec-1"}]}))
    return fake, notified


def batch_payload(fake):
    calls = fake.calls_to(BATCH_FRAGMENT)
    assert calls, "批量接口没有被调用"
    return calls[-1]["kwargs"]["json"]


def test_txt_mode_builds_ip_port_records(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    results = [("1.2.3.4:443#US", 10.0), ("5.6.7.8:443#JP", 5.0)]

    outcome = module.batch_update_cloudflare_dns(
        ["1.2.3.4", "5.6.7.8"], ip_info=ip_info(*[node for node, _ in results]),
        full_bw_results=results)

    assert outcome["updated"] is True
    assert outcome["record_type"] == "TXT"
    assert outcome["count"] == 2
    payload = batch_payload(fake)
    assert payload["deletes"] == [{"id": "rec-1"}]
    assert [post["content"] for post in payload["posts"]] == ["1.2.3.4:443", "5.6.7.8:443"]
    assert all(post["type"] == "TXT" for post in payload["posts"])


def test_a_mode_builds_ip_records_with_ttl_and_proxy(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "A"
    module.CF_TTL = 120
    module.CF_PROXIED = True
    results = [("1.2.3.4:443#US", 10.0)]

    outcome = module.batch_update_cloudflare_dns(
        ["1.2.3.4"], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=results)

    assert outcome["updated"] is True and outcome["record_type"] == "A"
    posts = batch_payload(fake)["posts"]
    assert posts == [{"name": "edge.example.com", "type": "A", "content": "1.2.3.4",
                      "ttl": 120, "proxied": True}]


def test_non_443_port_is_filtered(cf, module, capsys):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    results = [("1.2.3.4:8443#US", 10.0), ("5.6.7.8:443#US", 5.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info(*[node for node, _ in results]), full_bw_results=results)

    assert outcome["count"] == 1
    assert "非443端口过滤(1个)" in capsys.readouterr().out


def test_ipv6_only_nodes_are_filtered(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    module.FILTER_IPV6_AVAILABILITY = True
    results = [("1.2.3.4:443#US", 10.0), ("5.6.7.8:443#US", 5.0)]
    stacks = {"1.2.3.4:443#US": "ipv6_only", "5.6.7.8:443#US": "ipv4"}

    outcome = module.batch_update_cloudflare_dns([], ip_info=stacks, full_bw_results=results)

    assert outcome["count"] == 1
    assert batch_payload(fake)["posts"][0]["content"] == "5.6.7.8:443"


def test_blocked_countries_are_filtered(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    module.FILTER_BLOCKED_COUNTRIES_ENABLED = True
    module.BLOCKED_COUNTRIES = ["DE"]
    results = [("1.2.3.4:443#DE", 10.0), ("5.6.7.8:443#US", 5.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info(*[node for node, _ in results]), full_bw_results=results)

    assert outcome["count"] == 1
    assert batch_payload(fake)["posts"][0]["content"] == "5.6.7.8:443"


def test_duplicate_ips_are_deduplicated(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    results = [("1.2.3.4:443#US", 10.0), ("1.2.3.4:443#DE", 9.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info(*[node for node, _ in results]), full_bw_results=results)

    assert outcome["count"] == 1


def test_target_count_limits_records(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    module.DNS_UPDATE_TARGET_COUNT = 1
    results = [("1.2.3.4:443#US", 10.0), ("5.6.7.8:443#US", 5.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info(*[node for node, _ in results]), full_bw_results=results, target_count=None)

    assert outcome["count"] == 1
    assert len(batch_payload(fake)["posts"]) == 1


def test_risk_filter_keeps_clean_nodes(cf, module, monkeypatch):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    module.DNS_IP_RISK_FILTER_ENABLED = True
    module.DNS_IP_RISK_MAX_LEVEL = "轻微风险"
    monkeypatch.setattr(module, "get_ip_risk_level", lambda ip: "纯净")
    results = [("1.2.3.4:443#US", 10.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=results)

    assert outcome["count"] == 1


def test_risk_filter_falls_back_when_everything_is_risky(cf, module, monkeypatch):
    fake, notified = cf
    module.DNS_RECORD_TYPE = "TXT"
    module.DNS_IP_RISK_FILTER_ENABLED = True
    module.DNS_IP_RISK_MAX_LEVEL = "高风险"
    monkeypatch.setattr(module, "get_ip_risk_level", lambda ip: "极度危险")
    results = [("1.2.3.4:443#US", 10.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=results)

    assert outcome["updated"] is True
    assert outcome["count"] == 1
    assert notified and "风险等级" in notified[0]["summary"]


def test_risk_unknown_counts_as_filtered(cf, module, monkeypatch):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    module.DNS_IP_RISK_FILTER_ENABLED = True
    monkeypatch.setattr(module, "get_ip_risk_level", lambda ip: "未知")
    results = [("1.2.3.4:443#US", 10.0)]

    outcome = module.batch_update_cloudflare_dns(
        [], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=results)

    # 全部被判为未知 -> 回退到未风险过滤的列表，DNS 不会因为查询接口抽风被清空
    assert outcome["updated"] is True
    assert outcome["count"] == 1


def test_cf_disabled_skips_everything(cf, module):
    fake, _ = cf
    module.CF_ENABLED = False
    outcome = module.batch_update_cloudflare_dns(["1.2.3.4"], full_bw_results=[])
    assert outcome == {"updated": False, "skipped": "cf_disabled"}
    assert fake.calls == []


def test_unsupported_record_type_skips(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "AAAA"
    outcome = module.batch_update_cloudflare_dns(["1.2.3.4"], full_bw_results=[])
    assert outcome["skipped"] == "unsupported_record_type"
    assert fake.calls == []


def test_txt_mode_without_port_data_is_skipped(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "TXT"
    outcome = module.batch_update_cloudflare_dns(["1.2.3.4"], full_bw_results=None)
    assert outcome["skipped"] == "txt_requires_port"
    assert fake.calls == []


def test_a_mode_falls_back_to_ip_list(cf, module, capsys):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "A"
    outcome = module.batch_update_cloudflare_dns(["1.2.3.4", "5.6.7.8"], full_bw_results=None)
    assert outcome["updated"] is True
    assert [post["content"] for post in batch_payload(fake)["posts"]] == ["1.2.3.4", "5.6.7.8"]
    assert "降级使用 ip.txt 中的 IP" in capsys.readouterr().out


def test_no_candidates_skips_and_notifies(cf, module):
    fake, notified = cf
    module.DNS_RECORD_TYPE = "A"
    outcome = module.batch_update_cloudflare_dns([], full_bw_results=None)
    assert outcome["skipped"] == "no_candidates"
    assert notified and "DNS 更新跳过" in notified[0]["summary"]
    assert fake.calls == []


def test_api_failure_is_retried_then_reported(cf, module):
    fake, notified = cf
    module.DNS_RECORD_TYPE = "A"
    module.DNS_UPDATE_MAX_RETRIES = 2
    fake.route(BATCH_FRAGMENT, FakeResponse(json_data={"success": False, "errors": ["bad token"]}))

    outcome = module.batch_update_cloudflare_dns(
        ["1.2.3.4"], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=[("1.2.3.4:443#US", 1.0)])

    assert outcome["updated"] is False
    assert outcome["attempts"] == 2
    assert len(fake.calls_to(BATCH_FRAGMENT)) == 2
    assert notified and notified[-1]["summary"] == "DNS 更新失败"


def test_network_exception_is_retried(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "A"
    module.DNS_UPDATE_MAX_RETRIES = 2
    fake.route(BATCH_FRAGMENT, module.requests.exceptions.ConnectionError("boom"))

    outcome = module.batch_update_cloudflare_dns(
        ["1.2.3.4"], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=[("1.2.3.4:443#US", 1.0)])

    assert outcome["updated"] is False
    assert "boom" in outcome["error"]
    assert len(fake.calls_to(BATCH_FRAGMENT)) == 2


def test_list_failure_aborts_update(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "A"
    module.DNS_UPDATE_MAX_RETRIES = 1
    fake.route(LIST_FRAGMENT, FakeResponse(json_data={"success": False, "errors": ["nope"]}))

    outcome = module.batch_update_cloudflare_dns(
        ["1.2.3.4"], ip_info=ip_info("1.2.3.4:443#US"), full_bw_results=[("1.2.3.4:443#US", 1.0)])

    assert outcome["updated"] is False
    assert not fake.calls_to(BATCH_FRAGMENT)


def test_latency_is_printed_when_available(cf, module, capsys):
    module.DNS_RECORD_TYPE = "TXT"
    node = "1.2.3.4:443#US"
    module.batch_update_cloudflare_dns(
        [], ip_info=ip_info(node), full_bw_results=[(node, 8.0)], latency_map={node: 0.012})
    out = capsys.readouterr().out
    assert "8.00 Mbps" in out and "12.00 ms" in out


def test_dns_record_name_is_used_in_requests(cf, module):
    fake, _ = cf
    module.DNS_RECORD_TYPE = "A"
    module.batch_update_cloudflare_dns(["1.2.3.4"], full_bw_results=None)
    list_url = fake.calls_to(LIST_FRAGMENT)[0]["url"]
    assert "name=edge.example.com" in list_url
    assert fake.calls_to(LIST_FRAGMENT)[0]["kwargs"]["headers"]["Authorization"] == "Bearer token"


def test_txt_record_update_success_message(cf, module, capsys):
    module.DNS_RECORD_TYPE = "TXT"
    node = "1.2.3.4:443#US"
    module.batch_update_cloudflare_dns([], ip_info=ip_info(node), full_bw_results=[(node, 1.0)])
    assert "TXT 记录批量更新成功" in capsys.readouterr().out
