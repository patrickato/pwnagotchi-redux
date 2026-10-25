"""OUI enrichment: back-compat, randomized-MAC detection, classify, DB import."""
from redux.geo import oui


# ---- back-compat (the original API/fixtures still hold) ----------------- #

def test_backcompat_prefix_lookup_label():
    assert oui.oui_prefix("AA:BB:CC:DD:EE:FF") == "aabbcc"
    assert oui.lookup_oui("aa:bb:cc:11:22:33") == "DemoVendor"
    assert "DemoVendor" in oui.label_mac("aa:bb:cc:11:22:33")
    assert oui.lookup_oui("ff:ff:ff:ff:ff:ff") is None


def test_builtin_table_is_clean_and_real():
    import re
    for pref, vendor in oui._OUI.items():
        assert re.fullmatch(r"[0-9a-f]{6}", pref), f"bad prefix {pref!r}"
        assert vendor
    # a few real vendors landed
    assert oui.lookup_oui("b8:27:eb:00:00:01") == "RaspberryPi"
    assert oui.lookup_oui("24:0a:c4:00:00:01") == "Espressif"
    assert oui.lookup_oui("f0:d1:a9:00:00:01") == "Apple"


# ---- randomized / locally-administered ---------------------------------- #

def test_locally_administered_bit():
    # 0x02 set in first octet -> locally administered
    assert oui.is_locally_administered("02:11:22:33:44:55")
    assert oui.is_locally_administered("da:a1:19:aa:bb:cc")   # 0xda = ...1010, bit2 set
    assert not oui.is_locally_administered("b8:27:eb:00:00:01")  # Pi, globally unique


def test_multicast_bit():
    assert oui.is_multicast("01:00:5e:00:00:01")
    assert not oui.is_multicast("b8:27:eb:00:00:01")


def test_randomized_requires_la_and_unknown_vendor():
    # LA bit + unknown vendor -> randomized
    assert oui.is_randomized("da:a1:19:00:00:01")
    # globally-unique Pi -> not randomized
    assert not oui.is_randomized("b8:27:eb:00:00:01")


def test_bad_mac_is_safe():
    assert oui._first_octet("") is None
    assert not oui.is_locally_administered("")
    assert not oui.is_randomized("zz")


# ---- classify ------------------------------------------------------------ #

def test_classify_known_vendor_with_class_guess():
    c = oui.classify("b8:27:eb:00:00:01")
    assert c.vendor == "RaspberryPi" and c.device_class == "sbc"
    assert not c.randomized and "guess" in c.reason


def test_classify_randomized():
    c = oui.classify("da:a1:19:00:00:01")
    assert c.randomized and c.vendor is None
    assert "randomized" in c.reason


def test_classify_unknown():
    # a globally-unique (LA bit clear) prefix not in our table
    c = oui.classify("10:9a:dd:11:22:33")
    assert c.vendor is None and c.device_class is None and not c.randomized
    assert "not in table" in c.reason


# ---- full-DB import / merge --------------------------------------------- #

IEEE_SAMPLE = """\
OUI/MA-L                                                    Organization
company_id                                                  Organization
                                                            Address

00-1A-2B   (hex)\t\tAcme Networks, Inc.
001A2B     (base 16)\t\tAcme Networks, Inc.

AC-DE-48   (hex)\t\tPrivateCorp
"""

MANUF_SAMPLE = "3C:5A:B4\tGoogle\tGoogle LLC\n00:11:22\tExmpl\tExample Thing\n"

TSV_SAMPLE = "001a2b\tAcme Networks, Inc.\nacde48\tPrivateCorp\n"


def test_parse_ieee_hex_format():
    t = oui.parse_ieee(IEEE_SAMPLE)
    assert t["001a2b"] == "Acme Networks, Inc."
    assert t["acde48"] == "PrivateCorp"


def test_parse_manuf_format():
    t = oui.parse_ieee(MANUF_SAMPLE)
    assert t["3c5ab4"] == "Google LLC"
    assert t["001122"] == "Example Thing"


def test_parse_tsv_roundtrip():
    t = oui.parse_ieee(TSV_SAMPLE)
    assert t["001a2b"] == "Acme Networks, Inc."


def test_import_writes_tsv_and_full_table_merges(tmp_path):
    src = tmp_path / "oui.txt"; src.write_text(IEEE_SAMPLE)
    dest = tmp_path / "oui.tsv"
    n, reason = oui.import_ieee(str(src), str(dest))
    assert n == 2 and dest.is_file() and "imported" in reason
    merged = oui.full_table(extra_paths=[str(dest)])
    assert merged["001a2b"] == "Acme Networks, Inc."   # imported
    assert merged["b827eb"] == "RaspberryPi"           # built-in still there


def test_import_refuses_unparseable(tmp_path):
    src = tmp_path / "junk.txt"; src.write_text("not a vendor db at all\n")
    n, reason = oui.import_ieee(str(src), str(tmp_path / "out.tsv"))
    assert n == 0 and "refused" in reason


def test_full_table_skips_missing_files():
    t = oui.full_table(extra_paths=["/nonexistent/oui.tsv"])
    assert t["b827eb"] == "RaspberryPi"                # built-in intact
