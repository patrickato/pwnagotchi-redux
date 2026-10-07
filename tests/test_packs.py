import json
import pytest

from redux.packs import Pack, PackManager, DependencyError, from_dict, load_manifest


def _mkpack(d, name, **kw):
    p = d / name
    p.mkdir()
    manifest = {"name": name, **kw}
    (p / "pack.json").write_text(json.dumps(manifest))
    return p


def test_manifest_validation():
    with pytest.raises(ValueError):
        from_dict({})                       # no name
    with pytest.raises(ValueError):
        from_dict({"name": "x", "kind": "weird"})
    p = from_dict({"name": "viz", "kind": "suite", "requires": ["geo"]})
    assert p.name == "viz" and p.requires == ("geo",)


def test_load_toml_manifest(tmp_path):
    (tmp_path / "pack.toml").write_text('name = "kali-tools"\nkind = "tool"\napt = ["nmap", "aircrack-ng"]\n')
    p = load_manifest(tmp_path / "pack.toml")
    assert p.name == "kali-tools" and p.kind == "tool" and "nmap" in p.apt


def test_discovery_finds_packs(tmp_path):
    _mkpack(tmp_path, "geo", kind="suite")
    _mkpack(tmp_path, "viz", kind="suite", requires=["geo"])
    mgr = PackManager(tmp_path)
    assert {p.name for p in mgr.list()} == {"geo", "viz"}


def test_resolve_order_respects_requires(tmp_path):
    _mkpack(tmp_path, "base")
    _mkpack(tmp_path, "mid", requires=["base"])
    _mkpack(tmp_path, "top", requires=["mid"])
    mgr = PackManager(tmp_path)
    order = mgr.resolve_order(["top"])
    assert order.index("base") < order.index("mid") < order.index("top")


def test_missing_dependency_is_explained(tmp_path):
    _mkpack(tmp_path, "viz", requires=["geo"])   # geo not installed
    mgr = PackManager(tmp_path)
    with pytest.raises(DependencyError) as e:
        mgr.resolve_order(["viz"])
    assert "geo" in str(e.value) and "not installed" in str(e.value)


def test_cycle_is_detected(tmp_path):
    _mkpack(tmp_path, "a", requires=["b"])
    _mkpack(tmp_path, "b", requires=["a"])
    mgr = PackManager(tmp_path)
    with pytest.raises(DependencyError) as e:
        mgr.resolve_order(["a"])
    assert "cycle" in str(e.value)


def test_enable_pulls_in_dependencies(tmp_path):
    _mkpack(tmp_path, "geo")
    _mkpack(tmp_path, "viz", requires=["geo"])
    mgr = PackManager(tmp_path)
    newly = mgr.enable("viz")
    assert set(newly) == {"geo", "viz"}
    assert mgr.enabled_packs() == ["geo", "viz"]   # dependency order


def test_disable_cascades_to_dependents(tmp_path):
    _mkpack(tmp_path, "geo")
    _mkpack(tmp_path, "viz", requires=["geo"])
    mgr = PackManager(tmp_path)
    mgr.enable("viz")
    removed = mgr.disable("geo")                    # can't keep viz without geo
    assert set(removed) == {"geo", "viz"}
    assert mgr.enabled_packs() == []


def test_enabled_state_persists(tmp_path):
    _mkpack(tmp_path, "geo")
    PackManager(tmp_path).enable("geo")
    # a fresh manager on the same root reloads the enabled state
    assert PackManager(tmp_path).is_enabled("geo")
