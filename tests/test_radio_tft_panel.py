"""Radio page evidence/freshness tests: synthetic-only, no system reads."""
import pytest

from redux.display import fbui, radio_panel, preview, touch_nav


def measured(now=1000):
    return {"runtime": {"state":"running", "iface":"wlan0",
                         "capture_link_type":1, "updated_utc":now},
            "doctor": {"overall":"degraded", "findings":[]},
            "radio_inventory": {
                "schema":1, "sampled_utc":now,
                "radios":[
                    {"transport":"onboard","identity_stable":True,"iface":"wlan0",
                     "kernel_mode":"monitor","arphrd":1,"driver":"brcmfmac"},
                    {"transport":"usb","identity_stable":True,"iface":"wlan1",
                     "kernel_mode":"monitor","arphrd":803,"driver":"mt76x2u"},
                ],
            }}


def test_current_two_radios_do_not_fabricate_reception_or_ownership():
    r = radio_panel.model(measured(), now=1001)
    assert r["inventory"] == "CURRENT"
    assert r["capture"]["iface"] == "wlan0"
    assert r["capture"]["framing"] == "NON-NATIVE LINK"
    assert r["onboard"]["driver"] == "brcmfmac"
    assert r["usb"]["link"] == 803
    assert r["usb"]["reception"] == "UNVERIFIED"
    assert r["capture"]["reception"] == "UNVERIFIED"
    assert r["usb"]["state"] == "OBSERVED"
    assert fbui.render(measured(), page="radios", now=1001).size == (480, 320)


def test_new_radio_page_has_real_distinct_pixel_output():
    r = measured()
    frames = {p: fbui.render(r, page=p, now=1001).tobytes()
              for p in ("overview", "captures", "doctor", "radios")}
    assert len(set(frames.values())) == 4
    assert preview.build(report=r, pages=("radios",))["radios"].startswith(
        bytes.fromhex("89504e470d0a1a0a"))


def test_no_inventory_does_not_infer_usb_from_other_fields():
    r = measured()
    del r["radio_inventory"]
    result = radio_panel.model(r, now=1001)
    assert result["inventory"] == "NOT REPORTED"
    assert result["usb"]["state"] == "NOT REPORTED"
    assert result["capture"]["iface"] == "wlan0"


@pytest.mark.parametrize("change", [
    lambda i: i.update(schema=2),
    lambda i: i.update(sampled_utc=1),
    lambda i: i.update(sampled_utc=float("nan")),
    lambda i: i.update(radios=[]),
    lambda i: i["radios"].append(i["radios"][0]),
    lambda i: i["radios"][0].update(identity_stable=False),
    lambda i: i["radios"][0].update(transport="ethernet"),
    lambda i: i["radios"][0].update(arphrd=True),
    lambda i: i["radios"][1].update(driver="../../etc/passwd"),
    lambda i: i["radios"][1].update(iface="../../etc/passwd"),
    lambda i: i["radios"][1].update(kernel_mode="transmitting"),
])
def test_invalid_or_stale_radio_inventory_rejected_atomically(change):
    r = measured()
    change(r["radio_inventory"])
    result = radio_panel.model(r, now=1001)
    assert result["inventory"] in ("INVALID", "STALE", "NOT REPORTED")
    assert result["usb"]["state"] == "NOT REPORTED"
    assert result["onboard"]["state"] == "NOT REPORTED"


def test_stale_runtime_cannot_claim_current_capture_radio():
    r = measured()
    result = radio_panel.model(r, now=2000)
    assert result["capture"]["iface"] == "UNKNOWN"
    assert result["capture"]["state"] == "NOT CURRENT"
    assert result["capture"]["link"] is None
    assert result["usb"]["state"] == "NOT REPORTED"


def test_oversized_numeric_inputs_fail_closed():
    r = measured()
    r["runtime"]["capture_link_type"] = 10**1000
    r["radio_inventory"]["radios"][1]["arphrd"] = 10**1000
    assert radio_panel.model(r, now=1001)["inventory"] == "INVALID"
    r["radio_inventory"]["radios"][1]["arphrd"] = 803
    assert radio_panel.model(r, now=1001)["capture"]["link"] is None


def test_swipe_navigation_includes_fourth_page_and_wraps():
    assert touch_nav.navigate("doctor", 1) == "radios"
    assert touch_nav.navigate("radios", 1) == "trends"
    assert touch_nav.navigate("overview", -1) == "trends"


def test_radio_tft_shows_bettercap_onboard_and_usb_rows_separately(monkeypatch):
    import PIL.ImageDraw as image_draw
    labels = []
    real_draw = image_draw.Draw
    class Recorder:
        def __init__(self, image):
            self.inner = real_draw(image)
        def text(self, xy, label, *args, **kwargs):
            labels.append(label)
            return self.inner.text(xy, label, *args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.inner, name)
    monkeypatch.setattr(image_draw, "Draw", Recorder)
    frame = fbui.render(measured(), page="radios", now=1001)
    assert frame.size == (480, 320)
    assert "BETTERCAP / wlan0" in labels
    assert "ONBOARD / wlan0 / OBSERVED / brcmfmac" in labels
    assert "MODE MONITOR / LINK 1 / RX UNVERIFIED" in labels
    assert "USB / wlan1 / OBSERVED / mt76x2u" in labels
    assert "MODE MONITOR / LINK 803 / RX UNVERIFIED / OWNER UNVERIFIED" in labels
