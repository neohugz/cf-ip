"""命令行入口（--config/--output/--dry-run/--no-notify/--json/--strict/--check-config）测试。"""
import json

import pytest

MINIMAL = {"ADDITIONAL_SOURCES": [{"url": "https://example.invalid/all.txt", "enabled": True}]}


def stub_main(module, monkeypatch, stats_update=None, prints=None, exit_code=None):
    def fake_main(stats=None):
        stats = {} if stats is None else stats
        if prints:
            print(prints)
        stats.update(stats_update or {})
        if exit_code is not None:
            raise SystemExit(exit_code)
        return stats

    monkeypatch.setattr(module, "main", fake_main)


def test_parser_defaults(module):
    args = module.build_parser().parse_args([])
    assert args.config is None
    assert args.output is None
    assert (args.dry_run, args.no_notify, args.json, args.check_config, args.strict) == (False,) * 5


def test_parser_accepts_all_flags(module):
    args = module.build_parser().parse_args(
        ["-c", "a.json", "-o", "out.txt", "--dry-run", "--no-notify", "--json", "--strict"])
    assert args.config == "a.json" and args.output == "out.txt"
    assert args.dry_run and args.no_notify and args.json and args.strict


def test_version_flag_exits_zero(module, capsys):
    with pytest.raises(SystemExit) as excinfo:
        module.cli(["--version"])
    assert excinfo.value.code == 0
    assert "cfnb" in capsys.readouterr().out


def test_unknown_flag_exits_two(module):
    with pytest.raises(SystemExit) as excinfo:
        module.cli(["--definitely-not-a-flag"])
    assert excinfo.value.code == 2


def test_check_config_passes(module, config_file):
    path = config_file(MINIMAL)
    assert module.cli(["--config", str(path), "--check-config"]) == 0


def test_check_config_reports_bad_values(module, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**MINIMAL, "GLOBAL_TOP_N": 0}), encoding="utf-8")
    assert module.cli(["--config", str(bad), "--check-config"]) == 2


def test_check_config_wrong_type_returns_two(module, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**MINIMAL, "TCP_PROBES": "many"}), encoding="utf-8")
    assert module.cli(["--config", str(bad), "--check-config"]) == 2


def test_missing_config_file_exits_one(module, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        module.cli(["--config", str(tmp_path / "absent.json"), "--check-config"])
    assert excinfo.value.code == 1


def test_config_flag_reloads_values(module, config_file, monkeypatch):
    path = config_file({**MINIMAL, "GLOBAL_TOP_N": 9, "CF_ENABLED": False, "OUTPUT_FILE": "from-config.txt"})
    stub_main(module, monkeypatch, {"final_count": 1})
    assert module.cli(["--config", str(path)]) == 0
    assert module.GLOBAL_TOP_N == 9
    assert module.CF_ENABLED is False
    assert module.OUTPUT_FILE.endswith("from-config.txt")


def test_output_flag_overrides_config(module, monkeypatch):
    stub_main(module, monkeypatch, {"final_count": 1})
    assert module.cli(["--output", "custom.txt"]) == 0
    assert module.OUTPUT_FILE.endswith("custom.txt")
    assert module.OUTPUT_FILE.startswith(module.OUTPUT_DIR)


def test_dry_run_sets_global(module, monkeypatch):
    stub_main(module, monkeypatch, {"final_count": 1})
    assert module.cli(["--dry-run"]) == 0
    assert module.DRY_RUN is True


def test_no_notify_disables_wxpusher(module, monkeypatch):
    module.ENABLE_WXPUSHER = True
    stub_main(module, monkeypatch, {"final_count": 1})
    assert module.cli(["--no-notify"]) == 0
    assert module.ENABLE_WXPUSHER is False


def test_exit_code_from_main_is_propagated(module, monkeypatch):
    stub_main(module, monkeypatch, {"stopped": "no_nodes"}, exit_code=1)
    assert module.cli([]) == 1


def test_strict_returns_one_when_nothing_selected(module, monkeypatch):
    stub_main(module, monkeypatch, {"final_count": 0})
    assert module.cli(["--strict"]) == 1


def test_strict_returns_zero_when_nodes_selected(module, monkeypatch):
    stub_main(module, monkeypatch, {"final_count": 2})
    assert module.cli(["--strict"]) == 0


def test_json_output_keeps_progress_on_stderr(module, monkeypatch, capsys):
    stub_main(module, monkeypatch, {"final_count": 1, "final_nodes": ["1.2.3.4:443#US"]},
              prints="正在测速……")
    code = module.cli(["--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["final_count"] == 1
    assert payload["exit_code"] == 0
    assert payload["version"] == module.__version__
    assert "正在测速" in captured.err
    assert "正在测速" not in captured.out


def test_json_output_reports_early_exit(module, monkeypatch, capsys):
    stub_main(module, monkeypatch, {"stopped": "no_nodes"}, exit_code=1)
    code = module.cli(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["exit_code"] == 1
    assert payload["stopped"] == "no_nodes"


def test_json_strict_combination(module, monkeypatch, capsys):
    stub_main(module, monkeypatch, {"final_count": 0})
    code = module.cli(["--json", "--strict"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload["exit_code"] == 1


def test_start_logging_tees_and_restores(module, tmp_path, capsys):
    module.LOG_FILE = str(tmp_path / "run.log")
    original_stdout = module.sys.stdout
    handle = module.start_logging()
    assert handle is not None
    print("日志内容测试")
    module._close_log()
    assert module.sys.stdout is module.sys.__stdout__ or module.sys.stdout is original_stdout
    content = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "日志内容测试" in content


def test_start_logging_unwritable_path_is_reported(module, tmp_path, capsys):
    module.LOG_FILE = str(tmp_path / "missing-dir-with-file")
    (tmp_path / "missing-dir-with-file").write_text("我是文件，不是目录", encoding="utf-8")
    module.LOG_FILE = str(tmp_path / "missing-dir-with-file" / "run.log")
    assert module.start_logging() is None
    assert "无法打开日志文件" in capsys.readouterr().out


def test_close_log_is_idempotent(module):
    module._close_log()
    module._close_log()


def test_main_guard_present(module):
    """脚本仍然可以 `python3 main.py` 直接运行（保持原有用法）。"""
    from pathlib import Path
    source = Path(module.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert 'if __name__ == "__main__":\n    sys.exit(cli())' in source
