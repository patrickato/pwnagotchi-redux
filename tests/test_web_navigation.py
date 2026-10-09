"""Static DOM contract for the embedded 480x320-first Redux workspace.

The dynamically populated pages are read-only views of the supervisor, not
simulated radio controls. New pages may register via data-view/data-page.
"""
from html.parser import HTMLParser

from redux.web.status_page import render_page


class WorkspaceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.panels = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            assert a["id"] not in self.ids, f"duplicate DOM id: {a['id']}"
            self.ids.add(a["id"])
        if tag == "button" and a.get("data-view"):
            self.buttons.append(a)
        if a.get("data-page"):
            assert tag in {"div", "section"}, "workspace pane must be a region"
            self.panels.append(a)


def test_every_touch_navigation_page_has_real_content():
    page = render_page()
    parser = WorkspaceParser()
    parser.feed(page)
    expected = ["overview", "radio", "captures", "doctor"]
    assert [tab["data-view"] for tab in parser.buttons] == expected
    assert len(parser.panels) >= len(expected)
    assert {panel["data-page"] for panel in parser.panels} == set(expected)
    assert all(tab.get("type") == "button" for tab in parser.buttons)
    assert all(tab.get("aria-pressed") in {"true", "false"} for tab in parser.buttons)
    for panel in parser.panels:
        if panel["data-page"] != "overview":
            assert "hidden" in panel, "inactive pages must be hidden before JS starts"
    required = {
        "viewnav", "viewroot", "capturestate", "capturehanded",
        "capturepending", "capturefile", "capturewarning", "runtimestate",
        "runtimefree", "runtimelogs", "runtimeerror", "pipelinestate",
        "doctorheart", "doctorlist", "map", "aptbl",
        "devicebar", "engineindicator", "healthjump", "healthglyph",
        "herocapture", "face", "creature", "mood",
    }
    assert required <= parser.ids
    assert page.count('id="viewroot"') == 1
    assert '[hidden]{display:none!important}' in page


def test_page_navigation_is_inherently_extensible_and_read_only():
    page = render_page()
    assert "pageNames=navButtons.map" in page
    assert "viewPanels=Array.from(document.querySelectorAll" in page
    assert "setView(name,updateHash)" in page
    assert "aria-pressed" in page
    assert "hashchange" in page
    assert "onpointercancel" in page
    assert "Math.abs(dx)<65" in page
    assert "ArrowRight" in page and "ArrowLeft" in page
    assert "Home" in page and "End" in page
    assert "renderRuntimePanels(d)" in page
    assert "runtime.sightings_pending" in page
    assert "runtime.free_bytes" in page
    assert "/api/control" not in page  # read-only until an authenticated API exists
    assert "new WebSocket(" not in page


def test_compact_touch_workspace_keeps_one_scrollable_page_and_bottom_nav():
    page = render_page()
    assert "#viewnav{flex:0 0 auto;order:3" in page
    assert "#viewnav button{height:46px;min-height:46px" in page
    assert "#viewroot{flex:1;min-height:0;width:100%;overflow-y:auto" in page
    assert "overscroll-behavior:contain" in page
    assert "touch-action:manipulation" in page
    assert "document.addEventListener('visibilitychange'" in page
    assert ".hero-main{gap:11px" in page
    assert ".overview-metrics .row{grid-template-columns:repeat(3" in page
    assert "@media(max-width:360px)" in page
    assert ".health-shortcut{height:44px;min-height:44px" in page
    assert "#skinbtn{min-height:44px" in page
    assert "@media(prefers-reduced-motion:reduce)" in page
    assert "health-glow" in page and "health-alert" in page
    assert "aria-label=\"Open Doctor: health unknown\"" in page
    assert "setView('doctor')" in page
    assert "lastDoctorFindings" in page

def test_theme_pack_registry_validates_palette_data_without_script_injection():
    page = render_page()
    for token in ("--bg", "--fg", "--acc", "--warn", "--crit",
                  "--hero-start", "--hero-mid", "--hero-end"):
        assert token in page
    assert "themeRegistry=Object.create(null)" in page
    assert "registerTheme(name,tokens)" in page
    assert "THEME_KEYS.includes(key)" in page
    assert "/^#[0-9a-fA-F]{6}$/" in page
    assert "document.body.style.setProperty(key,tokens[key])" in page
    assert "document.body.style.removeProperty(key)" in page
    assert "Object.prototype.hasOwnProperty.call(themeRegistry,name)" in page
    assert "localStorage.setItem('augur.theme',next)" in page
    assert "applySkin()" in page
    assert 'id="palettebtn"' in page
    assert "data-theme" in page
    assert "registerTheme('signal'" in page
    assert "registerTheme('ember'" in page
    assert "registerTheme('glacier'" in page
    assert "eval(" not in page


