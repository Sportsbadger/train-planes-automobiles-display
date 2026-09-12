from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "src"))

from config import loadConfig  # noqa: E402


def test_adsb_config_defaults_to_disabled_train_only(monkeypatch):
    for key in [
        "adsbEnabled",
        "transportModes",
        "modeSwitchInterval",
        "modeRunCount",
        "lastLineText",
        "adsbHomeLat",
        "adsbHomeLon",
        "adsbUserAgent",
        "adsbRouteLookupEnabled",
        "adsbRouteApiUrl",
        "adsbRouteFetchTimeout",
        "adsbRouteDisplay",
        "adsbTopLeftTemplate",
        "adsbTopRightTemplate",
        "adsbScrollTemplate",
        "adsbNextLeftTemplate",
        "adsbNextRightTemplate",
        "adsbRecordsStorePath",
        "adsbRecordsWindows",
        "planeAlertEnabled",
        "planeAlertSourceUrl",
        "planeAlertFetchTimeout",
        "planeAlertUserAgent",
        "planeAlertDisplayCount",
        "planeAlertTimeOffset",
        "planeAlertTopLeftTemplate",
        "planeAlertTopRightTemplate",
        "planeAlertScrollTemplate",
        "planeAlertNextLeftTemplate",
        "planeAlertNextRightTemplate",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = loadConfig()

    assert config["adsb"]["enabled"] is False
    assert config["transport"]["modes"] == "train"
    assert config["transport"]["modeSwitchInterval"] == 300
    assert config["transport"]["modeRunCount"] is None
    assert config["transport"]["lastLineText"] == "****Last Line****"
    assert config["adsb"]["homeLat"] is None
    assert config["adsb"]["homeLon"] is None
    assert config["adsb"]["userAgent"].startswith("Mozilla/5.0")
    assert "Chrome/" in config["planeAlert"]["userAgent"]
    assert config["adsb"]["routeLookupEnabled"] is False
    assert "routeset" in config["adsb"]["routeApiUrl"]
    assert config["adsb"]["routeFetchTimeout"] == 4.0
    assert config["adsb"]["routeDisplay"] == "iata"
    assert (
        config["adsb"]["topLeftTemplate"]
        == "{position_ordinal}  {summary_left}"
    )
    assert (
        config["adsb"]["topRightTemplate"]
        == "{aircraft_type}  {summary_speed}  {distance}  {altitude}"
    )
    assert config["adsb"]["scrollTemplate"] == "{detail}"
    assert config["adsb"]["nextLeftTemplate"] == "{loop_aircraft}"
    assert config["adsb"]["nextRightTemplate"] == "{loop_info}"
    assert config["adsb"]["recordsStorePath"] == "/data/adsb-records.json"
    assert config["adsb"]["recordsWindows"] == ["day", "week", "forever"]
    assert config["planeAlert"]["enabled"] is False
    assert ":8083/cgi/stream.sh" in config["planeAlert"]["sourceUrl"]
    assert "mode=plane-alert" in config["planeAlert"]["sourceUrl"]
    assert config["planeAlert"]["fetchTimeout"] == 90.0
    assert config["planeAlert"]["displayCount"] == 30
    assert config["planeAlert"]["timeOffsetHours"] == 0.0
    assert (
        config["planeAlert"]["topLeftTemplate"]
        == "{position_ordinal}  {summary_left}"
    )
    assert config["planeAlert"]["topRightTemplate"] == "{summary_right}"
    assert config["planeAlert"]["scrollTemplate"] == "{detail}"
    assert config["planeAlert"]["nextLeftTemplate"] == "{loop_alert}"
    assert (
        config["planeAlert"]["nextRightTemplate"]
        == "{db_category}  {loop_info}"
    )


def test_adsb_config_parses_enabled_values(monkeypatch):
    monkeypatch.setenv("adsbEnabled", "True")
    monkeypatch.setenv("transportModes", "train,adsb")
    monkeypatch.setenv("modeSwitchInterval", "60")
    monkeypatch.setenv("modeRunCount", "2")
    monkeypatch.setenv("lastLineText", "-- END --")
    monkeypatch.setenv("adsbHomeLat", "51.5")
    monkeypatch.setenv("adsbHomeLon", "-0.1")
    monkeypatch.setenv("adsbFetchTimeout", "0")
    monkeypatch.setenv("adsbUserAgent", "CustomAgent/1.0")
    monkeypatch.setenv("adsbDisplayCount", "0")
    monkeypatch.setenv("adsbRouteLookupEnabled", "True")
    monkeypatch.setenv("adsbRouteApiUrl", "https://api.example.test/routeset")
    monkeypatch.setenv("adsbRouteFetchTimeout", "0")
    monkeypatch.setenv("adsbRouteDisplay", "city")
    monkeypatch.setenv("adsbTopLeftTemplate", "{display_name}")
    monkeypatch.setenv("adsbTopRightTemplate", "{altitude}")
    monkeypatch.setenv("adsbScrollTemplate", "{description} {seen}")
    monkeypatch.setenv("adsbNextLeftTemplate", "{position_ordinal} {flight}")
    monkeypatch.setenv("adsbNextRightTemplate", "{distance}")
    monkeypatch.setenv("planeAlertEnabled", "True")
    monkeypatch.setenv("planeAlertFetchTimeout", "0")
    monkeypatch.setenv("planeAlertDisplayCount", "0")
    monkeypatch.setenv("planeAlertMaxAgeHours", "12")
    monkeypatch.setenv("planeAlertTimeOffset", "1")
    monkeypatch.setenv("planeAlertTopLeftTemplate", "{display_name}")
    monkeypatch.setenv("planeAlertTopRightTemplate", "{tail_or_hex}")
    monkeypatch.setenv("planeAlertScrollTemplate", "{detail}")
    monkeypatch.setenv(
        "planeAlertNextLeftTemplate",
        "{position_ordinal} {display_name}",
    )
    monkeypatch.setenv("planeAlertNextRightTemplate", "{equipment} {time}")

    config = loadConfig()

    assert config["adsb"]["enabled"] is True
    assert config["transport"]["modes"] == "train,adsb"
    assert config["transport"]["modeSwitchInterval"] == 60
    assert config["transport"]["modeRunCount"] == 2
    assert config["transport"]["lastLineText"] == "-- END --"
    assert config["adsb"]["homeLat"] == 51.5
    assert config["adsb"]["homeLon"] == -0.1
    assert config["adsb"]["fetchTimeout"] == 0.1
    assert config["adsb"]["userAgent"] == "CustomAgent/1.0"
    assert config["adsb"]["displayCount"] == 1
    assert config["adsb"]["routeLookupEnabled"] is True
    assert config["adsb"]["routeApiUrl"] == "https://api.example.test/routeset"
    assert config["adsb"]["routeFetchTimeout"] == 0.1
    assert config["adsb"]["routeDisplay"] == "city"
    assert config["adsb"]["topLeftTemplate"] == "{display_name}"
    assert config["adsb"]["topRightTemplate"] == "{altitude}"
    assert config["adsb"]["scrollTemplate"] == "{description} {seen}"
    assert config["adsb"]["nextLeftTemplate"] == "{position_ordinal} {flight}"
    assert config["adsb"]["nextRightTemplate"] == "{distance}"
    assert config["planeAlert"]["enabled"] is True
    assert config["planeAlert"]["fetchTimeout"] == 0.1
    assert config["planeAlert"]["displayCount"] == 1
    assert config["planeAlert"]["maxAgeHours"] == 12.0
    assert config["planeAlert"]["timeOffsetHours"] == 1.0
    assert config["planeAlert"]["topLeftTemplate"] == "{display_name}"
    assert config["planeAlert"]["topRightTemplate"] == "{tail_or_hex}"
    assert config["planeAlert"]["scrollTemplate"] == "{detail}"
    assert (
        config["planeAlert"]["nextLeftTemplate"]
        == "{position_ordinal} {display_name}"
    )
    assert config["planeAlert"]["nextRightTemplate"] == "{equipment} {time}"


def test_plane_alert_display_count_caps_at_latest_30(monkeypatch):
    monkeypatch.setenv("planeAlertDisplayCount", "99")

    config = loadConfig()

    assert config["planeAlert"]["displayCount"] == 30


def test_transport_modes_default_to_enabled_optional_boards(monkeypatch):
    monkeypatch.delenv("transportModes", raising=False)
    monkeypatch.setenv("adsbEnabled", "True")
    monkeypatch.setenv("planeAlertEnabled", "True")

    config = loadConfig()

    assert config["transport"]["modes"] == "train,adsb,plane-alert"


def test_transport_modes_default_to_plane_alert_when_enabled(monkeypatch):
    monkeypatch.delenv("transportModes", raising=False)
    monkeypatch.delenv("adsbEnabled", raising=False)
    monkeypatch.setenv("planeAlertEnabled", "True")

    config = loadConfig()

    assert config["transport"]["modes"] == "train,plane-alert"


def test_transport_modes_treats_train_env_as_default_when_plane_alert_enabled(
    monkeypatch,
):
    monkeypatch.setenv("transportModes", "train")
    monkeypatch.delenv("adsbEnabled", raising=False)
    monkeypatch.setenv("planeAlertEnabled", "True")

    config = loadConfig()

    assert config["transport"]["modes"] == "train,plane-alert"


def test_transport_modes_plane_alert_entry_enables_plane_alert(monkeypatch):
    monkeypatch.setenv("transportModes", "train,plane-alert")
    monkeypatch.delenv("planeAlertEnabled", raising=False)

    config = loadConfig()

    assert config["planeAlert"]["enabled"] is True
    assert config["transport"]["modes"] == "train,plane-alert"


def test_transport_modes_adsb_entry_enables_adsb(monkeypatch):
    monkeypatch.setenv("transportModes", "train,adsb")
    monkeypatch.delenv("adsbEnabled", raising=False)

    config = loadConfig()

    assert config["adsb"]["enabled"] is True
    assert config["transport"]["modes"] == "train,adsb"


def test_adsb_records_mode_enables_adsb_and_store_path(monkeypatch):
    monkeypatch.setenv("transportModes", "train,adsb-records")
    monkeypatch.setenv("adsbRecordsStorePath", "/tmp/display-records.json")
    monkeypatch.setenv("adsbRecordsWindows", "24hr,All Time")

    config = loadConfig()

    assert config["adsb"]["enabled"] is True
    assert config["transport"]["modes"] == "train,adsb-records"
    assert config["adsb"]["recordsStorePath"] == "/tmp/display-records.json"
    assert config["adsb"]["recordsWindows"] == ["day", "forever"]
