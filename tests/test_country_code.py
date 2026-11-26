"""国家代码识别（自适应解析引擎的标签部分）测试。"""
import pytest


@pytest.mark.parametrize("label,expected", [
    ("US", "US"),
    ("us", "US"),
    ("Us", "US"),
    ("USA", "US"),
    ("usa", "US"),
    ("BRA", "BR"),
    ("DEU", "DE"),
    ("加拿大 CA", "CA"),
    ("JP 东京", "JP"),
])
def test_two_and_three_letter_codes(module, label, expected):
    assert module.extract_country_code(label) == expected


def test_lowercase_codes_are_resolved(module):
    """回归：小写来源以前识别不出国家，会把每个节点都送去备用 API 查询。"""
    assert module.extract_country_code("us") == "US"
    assert module.extract_country_code("de") == "DE"
    assert module.extract_country_code("usa") == "US"
    assert module.extract_country_code("01. de | premium") == "DE"


@pytest.mark.parametrize("label,expected", [
    ("美国", "US"),
    ("中国", "CN"),
    ("德国 01", "DE"),
    ("日本", "JP"),
    ("刚果（布）", "CG"),
    ("刚果（金）", "CD"),
])
def test_chinese_country_names(module, label, expected):
    assert module.extract_country_code(label) == expected


@pytest.mark.parametrize("label,expected", [
    ("🇺🇸", "US"),
    ("🇩🇪", "DE"),
    ("🇯🇵 日本 01", "JP"),
    ("🇺🇸 promoted", "US"),
])
def test_flag_emoji(module, label, expected):
    assert module.extract_country_code(label) == expected


def test_first_flag_wins_when_multiple(module):
    """两个国旗时取第一个（现有行为，避免以后误改成随机取值）"""
    assert module.extract_country_code("🇺🇸🇬🇧") == "US"


@pytest.mark.parametrize("label", [
    "",
    "   ",
    "01",
    "🇺🇸"[0],            # 半个国旗（单个区域指示符）
    "United States",     # 英文国家名不受支持
    "(US)",              # 带括号的代码不识别
    "PRE",               # 非 ISO 的三字母不会被当成 PR + E
    "pro",
    "延时最低",
])
def test_unrecognised_labels_return_none(module, label):
    assert module.extract_country_code(label) is None


def test_prefix_symbols_are_stripped(module):
    assert module.extract_country_code("#US") == "US"
    assert module.extract_country_code("01-US") == "US"
    # 前后标点会被容忍（去掉前缀符号后剩下的两位仍是有效代码）
    assert module.extract_country_code("_de_") == "DE"
    assert module.extract_country_code("DE01") == "DE"


def test_separators_split_tokens(module):
    assert module.extract_country_code("01,us;premium") == "US"
    assert module.extract_country_code("01|de|promo") == "DE"
    assert module.extract_country_code("01/us") == "US"


def test_code_set_matches_mapping(module):
    assert module.CODE_SET == set(module.CN_TO_CODE.values())
    assert all(len(entry) == 2 for entry in module.ALPHA3_TO_ALPHA2.values())
