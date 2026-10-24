"""Daemon dashboard bind scope — exposes the REAL live dashboard to a phone on
the LAN, fail-closed (never off-box without a token)."""
from redux.core.live_runtime import resolve_web_bind, LiveConfig


def test_localhost_binds_loopback_no_token():
    assert resolve_web_bind("localhost", "") == ("127.0.0.1", None, "")


def test_lan_with_token_binds_all_interfaces():
    host, tok, err = resolve_web_bind("lan", "s3cret")
    assert host == "0.0.0.0" and tok == "s3cret" and err == ""


def test_offbox_without_token_fails_closed_to_loopback():
    host, tok, err = resolve_web_bind("lan", "")
    assert host == "127.0.0.1" and tok is None and "token is required" in err
    host2, _, err2 = resolve_web_bind("tailscale", "")
    assert host2 == "127.0.0.1" and err2        # never exposed off-box unauthenticated


def test_unknown_scope_defaults_to_loopback():
    assert resolve_web_bind("bananas", "x")[0] == "127.0.0.1"


def test_liveconfig_loads_web_bind_fields(tmp_path):
    cfg = tmp_path / "live.toml"
    cfg.write_text('[live]\nweb_bind_scope = "lan"\nweb_token = "abc123"\n')
    c = LiveConfig.load(str(cfg))
    assert c.web_bind_scope == "lan" and c.web_token == "abc123"


def test_liveconfig_web_defaults_localhost(tmp_path):
    cfg = tmp_path / "live.toml"
    cfg.write_text('[live]\n')
    c = LiveConfig.load(str(cfg))
    assert c.web_bind_scope == "localhost" and c.web_token == ""
