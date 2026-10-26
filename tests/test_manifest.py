"""Capability manifest — the honest option-space catalog + readiness ladder."""
from redux.core import manifest as M
from redux.core.manifest import Feature, Readiness


def test_catalog_is_coherent():
    ids = [f.id for f in M.FEATURES]
    assert len(ids) == len(set(ids)), "duplicate feature id"
    for f in M.FEATURES:
        assert f.category in M.CATEGORIES, f"{f.id}: bad category {f.category}"
        assert f.title and f.summary
        # related ids must resolve
        for r in f.related:
            assert M.get(r) is not None, f"{f.id} -> unknown related {r}"
    # every readiness state has a glyph
    for r in Readiness:
        assert r in M.GLYPH


def test_get_is_case_insensitive():
    assert M.get("WIFI.CAPTURE").id == "wifi.capture"
    assert M.get("nope") is None


def _probes(present_bins=(), present_imports=(), hw=False):
    return dict(
        which=lambda b: ("/usr/bin/" + b) if b in present_bins else None,
        has_import=lambda m: m in present_imports,
        hardware_present=lambda f: hw,
    )


def test_readiness_planned_ignores_installed_tools():
    # an unimplemented feature stays PLANNED even if its tools are all present
    eap = M.get("eap.enterprise")
    assert eap.implemented is False
    state, reason = M.readiness(eap, **_probes(present_bins=("hostapd",), hw=True))
    assert state is Readiness.PLANNED
    assert "plan" in reason.lower() or "not" in reason.lower()


def test_readiness_tool_missing_then_needs_hardware_then_ready():
    wps = M.get("wifi.wps")              # implemented, needs reaver + a radio
    # nothing installed → TOOL_MISSING, naming the real install path
    s1, r1 = M.readiness(wps, **_probes())
    assert s1 is Readiness.TOOL_MISSING and "reaver" in r1 and "toolbox install" in r1
    # binary present but hardware unconfirmed → NEEDS_HARDWARE
    s2, r2 = M.readiness(wps, **_probes(present_bins=("reaver",)))
    assert s2 is Readiness.NEEDS_HARDWARE and "attach" in r2
    # binary present AND hardware attached → READY
    s3, _ = M.readiness(wps, **_probes(present_bins=("reaver",), hw=True))
    assert s3 is Readiness.READY


def test_readiness_counts_python_imports_not_just_binaries():
    gps = M.get("gps.position")          # imports `serial`, needs a GPS
    s_missing, r = M.readiness(gps, **_probes())
    assert s_missing is Readiness.TOOL_MISSING and "py:serial" in r
    s_ok, _ = M.readiness(gps, **_probes(present_imports=("serial",), hw=True))
    assert s_ok is Readiness.READY


def test_no_hardware_feature_is_ready_once_deps_present():
    crack = M.get("wifi.crack")          # no needs_hardware; needs hashcat + hcxtools
    assert crack.needs_hardware == ""
    s, _ = M.readiness(crack, **_probes(present_bins=("hashcat", "hcxpcapngtool")))
    assert s is Readiness.READY          # READY without any hardware probe


def test_power_tier_features_are_flagged_and_planned():
    # the dangerous RF-transmit features are power-tier AND honestly not wired yet
    for fid in ("subghz.replay", "ir.blast"):
        f = M.get(fid)
        assert f.power_tier is True
        assert f.implemented is False     # gated plan, not a wired runner
        assert M.readiness(f)[0] is Readiness.PLANNED


def test_completion_never_counts_a_plan_as_ready():
    # everything implemented + fully present + hardware attached
    c = M.completion(hardware_present=lambda f: True,
                     which=lambda b: "/usr/bin/" + b,
                     has_import=lambda m: True)
    planned = sum(1 for f in M.FEATURES if not f.implemented)
    assert c["planned"] == planned
    assert c["by_state"]["planned"] == planned          # plans never promote to ready
    assert c["by_state"]["ready"] == len(M.FEATURES) - planned
    assert c["implemented"] + c["planned"] == c["total"]


def test_links_point_at_real_toolbox_and_guidance_entries():
    from redux import toolbox as TB, guidance as G
    for f in M.FEATURES:
        if f.toolbox:
            assert TB.get(f.toolbox) is not None, f"{f.id} -> unknown toolbox {f.toolbox}"
        if f.guidance:
            assert G.get(f.guidance) is not None, f"{f.id} -> unknown guidance {f.guidance}"
        if f.cap:
            # linked capability ids are real members of the Cap vocabulary
            from redux.core.capabilities import Cap
            assert f.cap in {c.value for c in Cap}, f"{f.id} -> unknown cap {f.cap}"
