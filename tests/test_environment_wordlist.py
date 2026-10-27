"""Environment-grown wordlists — mine seeds from everything the box observed."""
from redux.crack.environment import EnvironmentHarvester, _tokenize


def test_tokenize_splits_names_into_seed_words():
    assert _tokenize("NetgearGuest5G") == ["netgear"]        # guest/5g are stopwords
    assert _tokenize("SmithFamily2019") == ["smith", "family", "2019"]
    assert _tokenize("the_wifi") == []                       # both stopwords


def test_harvests_ssid_whole_and_tokens():
    h = EnvironmentHarvester()
    h.add_ssid("SmithFamily2019")
    seeds = h.seeds()
    assert "SmithFamily2019" in seeds          # whole SSID kept (mutates best)
    assert "smith" in seeds and "family" in seeds


def test_cross_source_token_outranks_single_source():
    h = EnvironmentHarvester()
    h.add_ssid("Belkin")                       # token 'belkin' from one source
    h.add_pnl(["JohnsPhone"])                  # 'johns','phone' from pnl
    h.add_file_strings(["johns backup notes"]) # 'johns' again from loot → 2 sources
    ranked = dict(h.ranked_tokens())
    assert set(ranked["johns"]) == {"pnl", "loot"}     # provenance recorded
    # 'johns' seen from 2 sources ranks above single-source tokens
    order = [t for t, _ in h.ranked_tokens()]
    assert order.index("johns") < order.index("belkin")


def test_learn_triage_pulls_strings_metadata_and_filename():
    h = EnvironmentHarvester()
    h.learn_triage({
        "/loot/host_Data_vacation_photos.jpg": {
            "tools": {"strings": {"secret_hits": ["RiveraHousehold"]},
                      "exiftool": {"metadata": "Author: Maria Rivera"}}}})
    seeds = [s.lower() for s in h.seeds()]
    assert "rivera" in seeds and "maria" in seeds          # from metadata
    assert "household" in seeds                            # from strings (camelCase split)
    assert "vacation" in seeds and "photos" in seeds        # from filename


def test_learn_access_points_folds_ssid_and_vendor():
    class _AP:
        ssid = "CoffeeHouse"
        bssid = "aa:bb:cc:dd:ee:ff"
    h = EnvironmentHarvester()
    h.learn_access_points([_AP()])
    assert "coffee" in h.seeds() and "house" in h.seeds()


def test_grow_feeds_forge_with_environment_seeds():
    h = EnvironmentHarvester()
    h.add_ssid("Linksys")
    h.add_pnl(["SummerHouse"])
    h.add_file_strings(["project falcon"])
    f = h.grow("Linksys")
    # forge produced candidates; environment tokens appear mutated among them
    joined = " ".join(f.words).lower()
    assert f.words                                   # non-empty wordlist
    assert "summerhouse" in joined or "summer" in joined
    assert "falcon" in joined


def test_summary_reports_provenance():
    h = EnvironmentHarvester()
    h.add_ssid("Acme")
    h.add_file_strings(["acme corp vault"])
    summ = h.summary()
    assert summ["unique_tokens"] >= 2
    assert summ["by_source"].get("loot", 0) >= 1 and summ["by_source"].get("ssid", 0) >= 1


def test_failsafe_on_empty_and_junk():
    h = EnvironmentHarvester()
    h.add_ssid("")
    h.add_pnl([None, ""])
    h.add_vendor("not-a-mac")
    assert h.seeds() == [] and h.summary()["unique_tokens"] == 0
