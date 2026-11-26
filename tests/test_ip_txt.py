"""ip.txt 生成（广告头尾、逐行后缀、速度/延迟展示）测试。"""
import pytest


def write(module, nodes, path, **kwargs):
    options = {
        "header_enabled": False, "header_lines": [],
        "footer_enabled": False, "footer_lines": [],
        "perline_enabled": False, "perline_text": "",
    }
    options.update(kwargs)
    return module.write_ip_txt(nodes, str(path), **options)


def test_plain_node_list(module, tmp_path):
    target = tmp_path / "ip.txt"
    written = write(module, ["1.2.3.4:443#US", "5.6.7.8:443#JP"], target)
    assert written == str(target)
    assert target.read_text(encoding="utf-8") == "1.2.3.4:443#US\n5.6.7.8:443#JP\n"


def test_header_and_footer(module, tmp_path):
    target = tmp_path / "ip.txt"
    write(module, ["1.2.3.4:443#US"], target,
          header_enabled=True, header_lines=["# 广告位", "# 二行"],
          footer_enabled=True, footer_lines=["# 底部"])
    assert target.read_text(encoding="utf-8") == "# 广告位\n# 二行\n1.2.3.4:443#US\n# 底部\n"


def test_perline_text_is_appended(module, tmp_path):
    target = tmp_path / "ip.txt"
    write(module, ["1.2.3.4:443#US"], target, perline_enabled=True, perline_text=" @channel")
    assert target.read_text(encoding="utf-8") == "1.2.3.4:443#US @channel\n"


def test_speed_and_latency_are_optional(module, tmp_path):
    target = tmp_path / "ip.txt"
    write(module, ["1.2.3.4:443#US"], target,
          speed_map={"1.2.3.4:443#US": 12.5}, latency_map={"1.2.3.4:443#US": 0.02},
          show_bandwidth=False, show_latency=False)
    assert target.read_text(encoding="utf-8") == "1.2.3.4:443#US\n"


def test_speed_and_latency_can_be_enabled_per_call(module, tmp_path):
    """回归：以前只能通过模块全局开关控制，函数参数无法覆盖。"""
    target = tmp_path / "ip.txt"
    module.IP_TXT_SHOW_BANDWIDTH = False
    module.IP_TXT_SHOW_LATENCY = False
    write(module, ["1.2.3.4:443#US"], target,
          speed_map={"1.2.3.4:443#US": 12.5}, latency_map={"1.2.3.4:443#US": 0.02},
          show_bandwidth=True, show_latency=True)
    assert target.read_text(encoding="utf-8") == "1.2.3.4:443#US 12.50 Mbps 20.00 ms\n"


def test_globals_still_drive_output(module, tmp_path):
    target = tmp_path / "ip.txt"
    module.IP_TXT_SHOW_BANDWIDTH = True
    module.IP_TXT_SHOW_LATENCY = False
    write(module, ["1.2.3.4:443#US"], target, speed_map={"1.2.3.4:443#US": 1.0})
    assert target.read_text(encoding="utf-8") == "1.2.3.4:443#US 1.00 Mbps\n"


def test_missing_speed_or_latency_is_silently_skipped(module, tmp_path):
    target = tmp_path / "ip.txt"
    write(module, ["1.2.3.4:443#US"], target, show_bandwidth=True, show_latency=True,
          speed_map={}, latency_map={})
    assert target.read_text(encoding="utf-8") == "1.2.3.4:443#US\n"


def test_parent_directory_is_created(module, tmp_path):
    target = tmp_path / "nested" / "deeper" / "ip.txt"
    written = write(module, ["1.2.3.4:443#US"], target)
    assert written == str(target)
    assert target.exists()


def test_unicode_is_preserved(module, tmp_path):
    target = tmp_path / "ip.txt"
    write(module, ["1.2.3.4:443#US"], target,
          header_enabled=True, header_lines=["# 免流节点，禁止转卖"],
          perline_enabled=True, perline_text="  # 节点")
    content = target.read_text(encoding="utf-8")
    assert "免流节点" in content and content.endswith("# 节点\n")


def test_empty_node_list_writes_only_ads(module, tmp_path):
    target = tmp_path / "ip.txt"
    write(module, [], target, footer_enabled=True, footer_lines=["# 结束"])
    assert target.read_text(encoding="utf-8") == "# 结束\n"


def test_overwrites_previous_content(module, tmp_path):
    target = tmp_path / "ip.txt"
    target.write_text("旧内容\n", encoding="utf-8")
    write(module, ["1.2.3.4:443#US"], target)
    assert "旧内容" not in target.read_text(encoding="utf-8")


def test_relative_output_path_is_absolute(module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written = write(module, ["1.2.3.4:443#US"], "ip.txt")
    assert written == str(tmp_path / "ip.txt")


@pytest.mark.parametrize("speed,expected", [(0.5, "0.50 Mbps"), (123.456, "123.46 Mbps")])
def test_speed_formatting(module, tmp_path, speed, expected):
    target = tmp_path / "ip.txt"
    write(module, ["1.2.3.4:443#US"], target, speed_map={"1.2.3.4:443#US": speed}, show_bandwidth=True)
    assert expected in target.read_text(encoding="utf-8")
