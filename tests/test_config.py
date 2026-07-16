"""설정 로드/직렬화 테스트."""
from photo_organizer.core.config import Config


def test_defaults():
    c = Config()
    assert c.hamming_threshold == 5
    assert c.thumb_size == 256
    assert isinstance(c.screenshot_name_patterns, tuple)


def test_roundtrip_dict():
    c = Config(hamming_threshold=9, blur_laplacian_threshold=50.0)
    d = c.to_dict()
    c2 = Config.from_dict(d)
    assert c2.hamming_threshold == 9
    assert c2.blur_laplacian_threshold == 50.0
    assert isinstance(c2.screenshot_name_patterns, tuple)


def test_from_dict_ignores_unknown_keys():
    c = Config.from_dict({"hamming_threshold": 3, "unknown_key": 123})
    assert c.hamming_threshold == 3


def test_from_dict_list_patterns_to_tuple():
    c = Config.from_dict({"screenshot_name_patterns": ["a", "b"]})
    assert c.screenshot_name_patterns == ("a", "b")


def test_load_missing_returns_defaults(tmp_path):
    assert Config.load(tmp_path / "nope.toml").hamming_threshold == 5
    assert Config.load(None).hamming_threshold == 5


def test_load_toml(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_text("[thresholds]\nhamming_threshold = 7\nblur_laplacian_threshold = 80.0\n")
    c = Config.load(p)
    assert c.hamming_threshold == 7
    assert c.blur_laplacian_threshold == 80.0
