"""main() 端到端流程测试：全程替身，不打网络、不跑 curl、不动 Cloudflare。"""
import pytest

from fakes import FakeTime

NODE_US_A = "1.2.3.4:443#US"
NODE_US_B = "5.6.7.8:443#US"
NODE_JP = "9.9.9.9:443#JP"
LATENCIES = {NODE_US_A: 0.001, NODE_US_B: 0.002, NODE_JP: 0.003}


@pytest.fixture
def pipeline(module, monkeypatch, tmp_path):
    """把所有外部副作用换成记录器，返回一个可断言的记录对象。"""
    record = {
        "fetched": [],
        "dns": [],
        "sync": 0,
        "notified": [],
        "events": [],
    }
    module.OUTPUT_FILE = str(tmp_path / "ip.txt")
    module.OUTPUT_DIR = str(tmp_path)
    monkeypatch.setattr(module, "GLOBAL_TOP_N", 2)
    monkeypatch.setattr(module, "PER_COUNTRY_TOP_N", 1)
    monkeypatch.setattr(module, "BANDWIDTH_CANDIDATES", 4)
    monkeypatch.setattr(module, "PROGRESS_PRINT_INTERVAL", 999)
    monkeypatch.setattr(module, "time", FakeTime())  # 免掉重试等待，测试保持毫秒级
    monkeypatch.setattr(module, "send_wxpusher_notification",
                        lambda **kwargs: record["notified"].append(kwargs))
    monkeypatch.setattr(module, "sync_to_github", lambda: record.__setitem__("sync", record["sync"] + 1) or True)
    monkeypatch.setattr(module, "batch_update_cloudflare_dns",
                        lambda ip_list, **kwargs: record["dns"].append(ip_list) or {"updated": True, "count": len(ip_list)})

    def fake_fetch(url):
        record["fetched"].append(url)
        return list(record.get("source_nodes", [NODE_US_A, NODE_US_B, NODE_JP]))

    monkeypatch.setattr(module, "fetch_additional_source", fake_fetch)

    def fake_test_node(node):
        # 延迟各不相同，保证 TCP 排序确定（真实 run 里相同延迟的顺序取决于线程完成顺序）
        country = node.split("#")[-1]
        return (node, LATENCIES[node], country, module.TCP_PROBES)

    monkeypatch.setattr(module, "test_node", fake_test_node)
    monkeypatch.setattr(module, "availability_filter_with_retry",
                        lambda candidates: (candidates, {node: "ipv4" for node in candidates}, {}))
    monkeypatch.setattr(module, "http_server_filter", lambda candidates, config: candidates)
    monkeypatch.setattr(module, "bandwidth_filter",
                        lambda candidates: [(node, 10.0 - index) for index, node in enumerate(candidates)])
    return record


def read_output(module):
    from pathlib import Path
    return Path(module.OUTPUT_FILE).read_text(encoding="utf-8")


def test_happy_path_writes_output_and_updates_dns(module, pipeline):
    stats = module.main()
    assert stats["final_count"] == 2
    assert read_output(module).splitlines() == [NODE_US_A, NODE_US_B]
    assert pipeline["dns"] == [["1.2.3.4", "5.6.7.8"]]
    assert pipeline["sync"] == 1
    assert stats["degraded"] == []
    assert stats["nodes_total"] == 3
    assert stats["tcp_passed"] == 3
    assert stats["bandwidth_measured"] == 3


def test_bandwidth_failure_still_writes_output(module, pipeline, monkeypatch):
    """回归：带宽测速全部失败时 speed_map 未定义 -> 流程在写文件前直接崩溃。"""
    monkeypatch.setattr(module, "bandwidth_filter", lambda candidates: [])
    stats = module.main()
    assert stats["degraded"] == ["bandwidth"]
    assert stats["final_count"] == 2
    assert read_output(module).splitlines() == [NODE_US_A, NODE_US_B]
    assert pipeline["dns"] == [["1.2.3.4", "5.6.7.8"]]


def test_bandwidth_failure_without_latency_does_not_print_fake_speed(module, pipeline, monkeypatch, capsys):
    monkeypatch.setattr(module, "bandwidth_filter", lambda candidates: [])
    module.main()
    out = capsys.readouterr().out
    assert "速度 0.00 Mbps" not in out


def test_dry_run_skips_dns_and_sync(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "DRY_RUN", True)
    stats = module.main()
    assert pipeline["dns"] == []
    assert pipeline["sync"] == 0
    assert stats["dns"] == {"updated": False, "skipped": "dry_run"}
    assert stats["dry_run"] is True
    assert stats["final_count"] == 2


def test_output_path_is_independent_of_cwd(module, pipeline, monkeypatch, tmp_path):
    """回归：以前 ip.txt 写在进程当前目录，定时任务换目录后就写到别处了。"""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert not (elsewhere / "ip.txt").exists()
    module.main()
    assert module.OUTPUT_FILE.endswith("ip.txt")
    assert not (elsewhere / "ip.txt").exists()
    assert read_output(module).splitlines() == [NODE_US_A, NODE_US_B]


def test_sources_are_merged_and_deduplicated(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "ADDITIONAL_SOURCES", [
        {"url": "https://one.invalid/all.txt", "enabled": True},
        {"url": "https://two.invalid/all.txt", "enabled": True},
        {"url": "https://three.invalid/all.txt", "enabled": False},
    ])
    pipeline["source_nodes"] = [NODE_US_A, NODE_US_B, NODE_JP]
    stats = module.main()
    assert pipeline["fetched"] == ["https://one.invalid/all.txt", "https://two.invalid/all.txt"]
    # 第二个来源的节点与第一个重复，全部被去重
    assert stats["sources"][0] == {"url": "https://one.invalid/all.txt", "parsed": 3, "added": 3}
    assert stats["sources"][1]["added"] == 0
    assert stats["nodes_total"] == 3


def test_malformed_source_node_is_skipped_not_fatal(module, pipeline, monkeypatch):
    """回归：缺端口的脏节点会在前置端口过滤时抛 IndexError。"""
    pipeline["source_nodes"] = [NODE_US_A, "bad-node-without-colon", NODE_US_B]
    stats = module.main()
    assert stats["nodes_total"] == 3  # 脏数据在解析层没有被过滤掉
    assert read_output(module).splitlines() == [NODE_US_A, NODE_US_B]


def test_pre_filter_port_removes_all_nodes(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "PRE_FILTER_PORT_ENABLED", True)
    monkeypatch.setattr(module, "PRE_FILTER_PORTS", ["8443"])
    with pytest.raises(SystemExit) as excinfo:
        module.main()
    assert excinfo.value.code == 0


def test_pre_filter_port_keeps_nodes(module, pipeline, monkeypatch):
    pipeline["source_nodes"] = [NODE_US_A, "9.9.9.9:8443#JP"]
    monkeypatch.setattr(module, "PRE_FILTER_PORT_ENABLED", True)
    monkeypatch.setattr(module, "PRE_FILTER_PORTS", ["443"])
    stats = module.main()
    assert stats["after_pre_filter_port"] == 1
    assert read_output(module).splitlines() == [NODE_US_A]


def test_pre_filter_blocked_countries(module, pipeline, monkeypatch):
    pipeline["source_nodes"] = [NODE_US_A, NODE_JP]
    monkeypatch.setattr(module, "PRE_FILTER_BLOCKED_ENABLED", True)
    monkeypatch.setattr(module, "PRE_FILTER_BLOCKED_COUNTRIES", ["JP"])
    stats = module.main()
    assert stats["after_pre_filter_blocked"] == 1


def test_pre_filter_blocked_countries_empty_exits(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "PRE_FILTER_BLOCKED_ENABLED", True)
    monkeypatch.setattr(module, "PRE_FILTER_BLOCKED_COUNTRIES", ["US", "JP"])
    with pytest.raises(SystemExit) as excinfo:
        module.main()
    assert excinfo.value.code == 0


def test_allowlist_filter_only_keeps_allowed(module, pipeline, monkeypatch):
    pipeline["source_nodes"] = [NODE_US_A, NODE_JP]
    monkeypatch.setattr(module, "FILTER_COUNTRIES_ENABLED", True)
    monkeypatch.setattr(module, "ALLOWED_COUNTRIES", ["US"])
    monkeypatch.setattr(module, "bandwidth_filter", lambda candidates: [(node, 5.0) for node in candidates])
    stats = module.main()
    assert stats["after_allowlist"] == 1
    assert read_output(module).splitlines() == [NODE_US_A]


def test_allowlist_filter_empty_exits(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "FILTER_COUNTRIES_ENABLED", True)
    monkeypatch.setattr(module, "ALLOWED_COUNTRIES", ["BR"])
    with pytest.raises(SystemExit) as excinfo:
        module.main()
    assert excinfo.value.code == 0


def test_no_nodes_exits_with_code_one(module, pipeline, monkeypatch):
    # 关掉两个前置过滤，才能走到「一个节点都没有」的分支
    monkeypatch.setattr(module, "PRE_FILTER_PORT_ENABLED", False)
    monkeypatch.setattr(module, "PRE_FILTER_BLOCKED_ENABLED", False)
    pipeline["source_nodes"] = []
    with pytest.raises(SystemExit) as excinfo:
        module.main()
    assert excinfo.value.code == 1


def test_no_tcp_pass_exits_cleanly(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "test_node", lambda node: None)
    with pytest.raises(SystemExit) as excinfo:
        module.main()
    assert excinfo.value.code == 0


def test_stats_record_stop_reason(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "PRE_FILTER_PORT_ENABLED", False)
    monkeypatch.setattr(module, "PRE_FILTER_BLOCKED_ENABLED", False)
    pipeline["source_nodes"] = []
    stats = {}
    with pytest.raises(SystemExit):
        module.main(stats)
    assert stats["stopped"] == "no_nodes"


def test_empty_result_after_port_filter_records_reason(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "PRE_FILTER_PORT_ENABLED", True)
    monkeypatch.setattr(module, "PRE_FILTER_PORTS", ["8443"])
    stats = {}
    with pytest.raises(SystemExit) as excinfo:
        module.main(stats)
    assert excinfo.value.code == 0
    assert stats["stopped"] == "pre_filter_port_empty"


def test_availability_degradation_is_recorded(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "TEST_AVAILABILITY", True)
    monkeypatch.setattr(module, "availability_filter_with_retry", lambda candidates: (candidates, {}, {}))
    stats = module.main()
    assert "availability" in stats["degraded"]
    assert stats["availability_passed"] == 3


def test_per_country_mode_picks_best_per_country(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "USE_GLOBAL_MODE", False)
    monkeypatch.setattr(module, "PER_COUNTRY_TOP_N", 1)
    pipeline["source_nodes"] = [NODE_US_A, NODE_US_B, NODE_JP]

    def fake_bw(candidates):
        speeds = {NODE_US_A: 1.0, NODE_US_B: 9.0, NODE_JP: 5.0}
        return sorted(((node, speeds[node]) for node in candidates if node in speeds),
                      key=lambda item: item[1], reverse=True)

    monkeypatch.setattr(module, "bandwidth_filter", fake_bw)
    stats = module.main()
    assert sorted(stats["final_nodes"]) == sorted([NODE_US_B, NODE_JP])


def test_per_country_mode_falls_back_without_bandwidth(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "USE_GLOBAL_MODE", False)
    monkeypatch.setattr(module, "bandwidth_filter", lambda candidates: [])
    pipeline["source_nodes"] = [NODE_US_A, NODE_US_B, NODE_JP]
    stats = module.main()
    assert sorted(stats["final_nodes"]) == sorted([NODE_US_A, NODE_JP])
    assert "bandwidth" in stats["degraded"]


def test_ads_and_flags_are_written(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "AD_HEADER_ENABLED", True)
    monkeypatch.setattr(module, "AD_HEADER_LINES", ["# 头部广告"])
    monkeypatch.setattr(module, "AD_PERLINE_ENABLED", True)
    monkeypatch.setattr(module, "AD_PERLINE_TEXT", "  # 广告")
    module.main()
    lines = read_output(module).splitlines()
    assert lines[0] == "# 头部广告"
    assert all(line.endswith("# 广告") for line in lines[1:])


def test_speed_and_latency_columns(module, pipeline, monkeypatch):
    monkeypatch.setattr(module, "IP_TXT_SHOW_BANDWIDTH", True)
    monkeypatch.setattr(module, "IP_TXT_SHOW_LATENCY", True)
    module.main()
    assert "Mbps" in read_output(module) and "ms" in read_output(module)


def test_dns_receives_ips_only(module, pipeline):
    module.main()
    assert pipeline["dns"][0] == ["1.2.3.4", "5.6.7.8"]
