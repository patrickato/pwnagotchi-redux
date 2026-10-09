from redux.core.scope import Scope
from redux.crack.capture import AngryOxideProvider, AngryOxideConfig
from redux.cli import build_parser


def test_passive_angryoxide_works_without_scope():
    provider = AngryOxideProvider(
        config=AngryOxideConfig(iface="wlan1mon"),
        which=lambda name: "/usr/bin/angryoxide"
    )
    plan = provider.plan(Scope(), active=False, iface="wlan1mon")
    assert plan.runnable
    assert plan.passive
    assert "--notransmit" in plan.argv
    assert "--autohunt" not in plan.argv
    assert "--autoexit" not in plan.argv


def test_active_angryoxide_still_requires_target():
    provider = AngryOxideProvider(which=lambda name: "/usr/bin/angryoxide")
    plan = provider.plan(Scope(), active=True, iface="wlan1mon")
    assert not plan.runnable
    assert plan.argv == []


def test_pipeline_cli_routes_commands_without_running():
    parser = build_parser()
    for command in ("status", "ingest", "audit"):
        parsed = parser.parse_args(["pipeline", command])
        assert parsed.pipeline_cmd == command
        assert parsed.func.__name__ == "cmd_pipeline"
