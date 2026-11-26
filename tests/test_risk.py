"""IP 风险等级（ipapi.is）判断逻辑测试。"""
import pytest

from fakes import FakeRequests, FakeResponse

RISK_URL = "https://api.ipapi.is"


@pytest.fixture
def fake(module, monkeypatch):
    fake_requests = FakeRequests()
    monkeypatch.setattr(module, "requests", fake_requests)
    return fake_requests


def risk_payload(company="0.0", asn="0.0", **flags):
    payload = {
        "company": {"abuser_score": company},
        "asn": {"abuser_score": asn},
    }
    payload.update(flags)
    return payload


@pytest.mark.parametrize("company,asn,expected", [
    ("0.0", "0.0", "极度纯净"),
    ("0.005", "0.005", "纯净"),        # 0.025 * 100 = 2.5%
    ("0.02", "0.02", "轻微风险"),      # 10%
    ("0.1", "0.1", "高风险"),          # 50%
    ("1.0", "1.0", "极度危险"),        # 500%
])
def test_thresholds(fake, module, company, asn, expected):
    fake.route(RISK_URL, FakeResponse(json_data=risk_payload(company, asn)))
    assert module.get_ip_risk_level("1.2.3.4") == expected


@pytest.mark.parametrize("company,asn", [
    ("0.25 (Very Low)", "1.0 (Very High)"),
    ("0.5", "0.5"),
    ("", "0"),
    ("n/a", "0"),
])
def test_score_string_formats_are_tolerated(fake, module, company, asn):
    """带括号/缺字段/非数字的分数不应让程序崩溃，解析不了就按 0 计。"""
    fake.route(RISK_URL, FakeResponse(json_data=risk_payload(company, asn)))
    assert module.get_ip_risk_level("1.2.3.4") in module.RISK_LEVEL_ORDER


def test_null_scores_are_tolerated(fake, module):
    fake.route(RISK_URL, FakeResponse(json_data={"company": {}, "asn": {"abuser_score": None}}))
    assert module.get_ip_risk_level("1.2.3.4") in module.RISK_LEVEL_ORDER


def test_flag_scores_are_added(fake, module):
    fake.route(RISK_URL, FakeResponse(json_data=risk_payload("0.004", "0.004")))
    assert module.get_ip_risk_level("1.2.3.4") == "纯净"  # 2%

    fake.route(RISK_URL, FakeResponse(json_data=risk_payload("0.004", "0.004", is_vpn=True, is_proxy=True)))
    assert module.get_ip_risk_level("1.2.3.4") == "高风险"  # +0.3 => 32%


def test_bogon_penalty_pushes_to_dangerous(fake, module):
    fake.route(RISK_URL, FakeResponse(json_data=risk_payload("0.5", "0.5", is_bogon=True)))
    assert module.get_ip_risk_level("1.2.3.4") == "极度危险"


def test_network_error_returns_unknown(fake, module):
    fake.route(RISK_URL, FakeResponse(status_code=500))
    assert module.get_ip_risk_level("1.2.3.4") == "未知"


def test_connection_error_returns_unknown(fake, module):
    fake.route(RISK_URL, module.requests.exceptions.ConnectTimeout("boom"))
    assert module.get_ip_risk_level("1.2.3.4") == "未知"


def test_non_json_body_returns_unknown(fake, module):
    fake.route(RISK_URL, FakeResponse(json_error=True))
    assert module.get_ip_risk_level("1.2.3.4") == "未知"


def test_missing_fields_do_not_crash(fake, module):
    fake.route(RISK_URL, FakeResponse(json_data={}))
    assert module.get_ip_risk_level("1.2.3.4") == "极度纯净"


def test_risk_order_covers_all_levels(module):
    assert sorted(module.RISK_LEVEL_ORDER.values()) == [0, 1, 2, 3, 4]
