"""Unified operator config.toml — defaults, partial merge, validation, template."""
import tomllib

import pytest

from redux.config import AugurConfig, template, MARKER


def test_defaults_are_sane_and_valid():
    c = AugurConfig()
    assert c.web.bind_scope == "localhost" and c.cache.retention_days == 30
    assert c.validate() == []                       # defaults validate clean (localhost)


def test_partial_dict_merges_over_defaults_and_ignores_unknown():
    c = AugurConfig.from_dict({"web": {"port": 9000, "bogus": 1}, "nope": {"x": 1}})
    assert c.web.port == 9000                        # overridden
    assert c.web.bind_scope == "localhost"           # default kept
    assert c.cache.db_path == AugurConfig().cache.db_path
    assert not hasattr(c.web, "bogus")               # unknown key dropped, not fatal


def test_load_from_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[web]\nbind_scope = "lan"\ntoken = "s3cret"\n[persona]\ndefault = "blue"\n')
    c = AugurConfig.load(p)
    assert c.web.bind_scope == "lan" and c.web.token == "s3cret" and c.persona.default == "blue"


def test_validate_flags_off_box_without_token():
    bad = AugurConfig.from_dict({"web": {"bind_scope": "lan"}})       # no token
    probs = bad.validate()
    assert any("web.token" in p for p in probs)
    ok = AugurConfig.from_dict({"web": {"bind_scope": "lan", "token": "t"}})
    assert ok.validate() == []


def test_validate_flags_bad_values():
    c = AugurConfig.from_dict({
        "web": {"bind_scope": "wan"},
        "cache": {"retention_days": -1, "max_rows": 0},
        "persona": {"default": "wizard"},
        "mesh": {"node_id": ""},
    })
    joined = " ".join(c.validate())
    assert "bind_scope" in joined and "retention_days" in joined and "max_rows" in joined
    assert "persona.default" in joined and "node_id" in joined


def test_validate_never_raises_on_mistyped_fields():
    # valid TOML, wrong types — validate() must REPORT, not crash (glass-box contract)
    c = AugurConfig.from_dict({"cache": {"retention_days": "30", "max_rows": "lots"},
                               "mesh": {"node_id": 5}})
    probs = c.validate()                    # must not raise
    j = " ".join(probs)
    assert "retention_days" in j and "max_rows" in j and "node_id" in j


def test_to_display_masks_token():
    c = AugurConfig.from_dict({"web": {"token": "s3cret"}})
    d = c.to_display()
    assert d["web"]["token"] == "(set)" and "s3cret" not in str(d)
    assert AugurConfig().to_display()["web"]["token"] == "(unset)"


def test_template_parses_and_validates_and_has_markers():
    text = template()
    assert MARKER in text                             # has USER-INPUT markers (in comments)
    parsed = tomllib.loads(text)                      # valid TOML
    cfg = AugurConfig.from_dict(parsed)
    assert cfg.validate() == []                       # ships valid on localhost
    assert MARKER not in cfg.web.token                # markers live in comments, not values
