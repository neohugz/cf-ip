"""自适应解析引擎测试：JSON / 文本 / 脏数据 / 备用国家查询。"""
import json

import pytest


@pytest.fixture
def no_country_api(module, monkeypatch):
    """把备用国家查询换成本地替身，避免测试打网络。"""
    calls = []

    def fake_resolve(ipports):
        calls.append(list(ipports))
        return {ipport: "US" for ipport in ipports}

    monkeypatch.setattr(module, "_resolve_countries_batch", fake_resolve)
    return calls


def test_empty_text(module):
    assert module.parse_adaptive("") == []
    assert module.parse_adaptive("   \n  ") == []


def test_plain_text_nodes(module):
    text = "1.2.3.4:443#US 5.6.7.8:8443#JP"
    assert module.parse_adaptive(text) == ["1.2.3.4:443#US", "5.6.7.8:8443#JP"]


def test_text_nodes_normalise_country(module):
    assert module.parse_adaptive("1.2.3.4:443#us") == ["1.2.3.4:443#US"]
    assert module.parse_adaptive("1.2.3.4:443#美国") == ["1.2.3.4:443#US"]


def test_text_skips_entries_without_label(module):
    assert module.parse_adaptive("1.2.3.4:443 5.6.7.8:443#US") == ["5.6.7.8:443#US"]


def test_text_skips_ipv6_and_malformed(module):
    text = "[2001:db8::1]:443#US 1.2.3.4:443#US ::1:443#DE nonsense"
    assert module.parse_adaptive(text) == ["1.2.3.4:443#US"]


def test_unknown_label_uses_backup_api(module, no_country_api):
    nodes = module.parse_adaptive("1.2.3.4:443#未知品牌 5.6.7.8:443#US")
    assert no_country_api == [["1.2.3.4:443"]]
    assert sorted(nodes) == ["1.2.3.4:443#US", "5.6.7.8:443#US"]


def test_backup_api_none_result_is_dropped(module, monkeypatch):
    monkeypatch.setattr(module, "_resolve_countries_batch", lambda ipports: {ipp: None for ipp in ipports})
    assert module.parse_adaptive("1.2.3.4:443#未知") == []


def test_json_list_of_objects(module):
    payload = json.dumps([
        {"ip": "1.2.3.4", "port": 443, "country": "US"},
        {"ip": "5.6.7.8", "port": "8443", "country": "jp"},
    ])
    assert module.parse_adaptive(payload) == ["1.2.3.4:443#US", "5.6.7.8:8443#JP"]


def test_json_nested_under_nodes_key(module):
    payload = json.dumps({"ok": True, "nodes": [{"host": "1.2.3.4", "port": 443, "cc": "DE"}]})
    assert module.parse_adaptive(payload) == ["1.2.3.4:443#DE"]


def test_json_nested_under_data_key(module):
    payload = json.dumps({"data": {"list": [{"ip": "1.2.3.4", "port": 443, "country": "GB"}]}})
    assert module.parse_adaptive(payload) == ["1.2.3.4:443#GB"]


def test_json_country_name_and_flag(module):
    payload = json.dumps([
        {"ip": "1.2.3.4", "port": 443, "country": "美国"},
        {"ip": "5.6.7.8", "port": 443, "country": "🇯🇵"},
    ])
    assert module.parse_adaptive(payload) == ["1.2.3.4:443#US", "5.6.7.8:443#JP"]


def test_json_skips_invalid_hosts_and_ports(module):
    payload = json.dumps([
        {"ip": "cdn.example.com", "port": 443, "country": "US"},
        {"ip": "1.2.3.4", "port": "https", "country": "US"},
        {"ip": "1.2.3.4", "country": "US"},
        {"ip": "1.2.3.4", "port": 443},
        {"ip": "5.6.7.8", "port": 443, "country": "unknown place"},
    ])
    assert module.parse_adaptive(payload) == []


def test_json_root_object_with_node_fields(module):
    payload = json.dumps({"ip": "1.2.3.4", "port": 443, "country": "US"})
    assert module.parse_adaptive(payload) == ["1.2.3.4:443#US"]


def test_json_list_of_strings_is_parsed_as_text(module):
    payload = json.dumps(["1.2.3.4:443#US", "5.6.7.8:443#DE"])
    assert module.parse_adaptive(payload) == ["1.2.3.4:443#US", "5.6.7.8:443#DE"]


def test_broken_json_falls_back_to_text(module):
    text = '{"nodes": 1.2.3.4:443#US} 5.6.7.8:443#DE'
    assert module.parse_adaptive(text) == ["1.2.3.4:443#US", "5.6.7.8:443#DE"]


def test_json_without_nodes_falls_back_to_text(module):
    """回归：以前 JSON 解析不出节点就直接返回空列表，文本内容被丢掉。"""
    text = '{"status": "ok"}\n1.2.3.4:443#US'
    assert module.parse_adaptive(text) == ["1.2.3.4:443#US"]


def test_json_without_nodes_and_without_text_returns_empty(module, capsys):
    assert module.parse_adaptive('{"status": "ok"}') == []
    assert "JSON 未解析出节点" in capsys.readouterr().out
