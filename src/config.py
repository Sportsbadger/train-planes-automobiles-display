import os
import re

from adsb_records import normalize_record_windows


DEFAULT_ADSB_RECORDS_STORE_PATH = "/data/adsb-records.json"
DEFAULT_ADSB_TOP_LEFT_TEMPLATE = "{position_ordinal}  {summary_left}"
DEFAULT_ADSB_TOP_RIGHT_TEMPLATE = (
    "{aircraft_type}  {summary_speed}  {distance}  {altitude}"
)
DEFAULT_ADSB_SCROLL_TEMPLATE = "{detail}"
DEFAULT_ADSB_NEXT_LEFT_TEMPLATE = "{loop_aircraft}"
DEFAULT_ADSB_NEXT_RIGHT_TEMPLATE = "{loop_info}"

DEFAULT_PLANE_ALERT_TOP_LEFT_TEMPLATE = "{position_ordinal}  {summary_left}"
DEFAULT_PLANE_ALERT_TOP_RIGHT_TEMPLATE = "{summary_right}"
DEFAULT_PLANE_ALERT_SCROLL_TEMPLATE = "{detail}"
DEFAULT_PLANE_ALERT_NEXT_LEFT_TEMPLATE = "{loop_alert}"
DEFAULT_PLANE_ALERT_NEXT_RIGHT_TEMPLATE = "{db_category}  {loop_info}"

DEFAULT_LAST_LINE_TEXT = "****Last Line****"
MAX_PLANE_ALERT_DISPLAY_COUNT = 30


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.upper() == "TRUE"


def _env_int(
    name: str,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = int(os.getenv(name) or default)
    if minimum is not None and value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def _env_float(
    name: str,
    default: float,
    minimum: float | None = None,
) -> float:
    value = float(os.getenv(name) or default)
    if minimum is not None and value < minimum:
        return minimum
    return value


def _env_optional_float(name: str) -> float | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    return float(value)


def _env_optional_int(
    name: str,
    minimum: int | None = None,
) -> int | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        return minimum
    return parsed

# validate platform number
def parsePlatformData(platform):
    if platform is None:
        return ""
    elif bool(re.match(r'^(?:\d{1,2}[A-D]|[A-D]|\d{1,2})$', platform)):
        return platform
    else:
        return ""


def _transport_mode_requested(raw_modes: str | None, mode_names: set[str]) -> bool:
    if raw_modes is None:
        return False
    return any(
        raw_mode.strip().lower() in mode_names
        for raw_mode in raw_modes.split(",")
    )


def _default_transport_modes(
    raw_modes: str | None,
    adsb_enabled: bool,
    plane_alert_enabled: bool,
) -> str:
    configured_modes = (raw_modes or "").strip()
    if configured_modes and configured_modes.lower() != "train":
        return configured_modes

    modes = ["train"]
    if adsb_enabled:
        modes.append("adsb")
    if plane_alert_enabled:
        modes.append("plane-alert")
    return ",".join(modes)


def loadConfig():
    data = {
        "journey": {},
        "api": {},
        "transport": {},
        "adsb": {},
        "planeAlert": {},
    }

    data["targetFPS"] = int(os.getenv("targetFPS") or 70)
    data["refreshTime"] = int(os.getenv("refreshTime") or 180)
    data["fpsTime"] = int(os.getenv("fpsTime") or 180)
    data["screenRotation"] = int(os.getenv("screenRotation") or 2)
    data["screenBlankHours"] = os.getenv("screenBlankHours") or ""
    data["headless"] = False
    if os.getenv("headless", "").upper() == "TRUE":
        data["headless"] = True

    data["debug"] = False
    if os.getenv("debug", "").upper() == "TRUE":
        data["debug"] = True
    else:
        if os.getenv("debug") and os.getenv("debug").isnumeric():
            data["debug"] = int(os.getenv("debug"))

    data["dualScreen"] = False
    if os.getenv("dualScreen", "").upper() == "TRUE":
        data["dualScreen"] = True
    data["firstDepartureBold"] = True
    if os.getenv("firstDepartureBold", "").upper() == "FALSE":
        data["firstDepartureBold"] = False
    data["hoursPattern"] = re.compile("^((2[0-3]|[0-1]?[0-9])-(2[0-3]|[0-1]?[0-9]))$")

    data["journey"]["departureStation"] = os.getenv("departureStation") or "PAD"

    data["journey"]["destinationStation"] = os.getenv("destinationStation") or ""
    if data["journey"]["destinationStation"] == "null" or data["journey"]["destinationStation"] == "undefined":
        data["journey"]["destinationStation"] = ""

    data["journey"]["individualStationDepartureTime"] = False
    if os.getenv("individualStationDepartureTime", "").upper() == "TRUE":
        data["journey"]["individualStationDepartureTime"] = True

    data["journey"]["outOfHoursName"] = os.getenv("outOfHoursName") or "London Paddington"
    data["journey"]["stationAbbr"] = {"International": "Intl."}
    data["journey"]['timeOffset'] = os.getenv("timeOffset") or "0"
    data["journey"]["screen1Platform"] = parsePlatformData(os.getenv("screen1Platform"))
    data["journey"]["screen2Platform"] = parsePlatformData(os.getenv("screen2Platform"))

    data["api"]["apiKey"] = os.getenv("apiKey") or None
    data["api"]["operatingHours"] = os.getenv("operatingHours") or ""

    data["showDepartureNumbers"] = False
    if os.getenv("showDepartureNumbers", "").upper() == "TRUE":
        data["showDepartureNumbers"] = True

    data["loopDepartureCount"] = int(os.getenv("loopDepartureCount") or 2)
    if data["loopDepartureCount"] < 2:
        data["loopDepartureCount"] = 2

    data["loopDepartureInterval"] = int(os.getenv("loopDepartureInterval") or 10)
    if data["loopDepartureInterval"] < 1:
        data["loopDepartureInterval"] = 1

    raw_transport_modes = os.getenv("transportModes")

    data["adsb"]["enabled"] = _env_bool(
        "adsbEnabled",
        False,
    ) or _transport_mode_requested(
        raw_transport_modes,
        {"adsb", "adsb-records", "adsb-stats", "records"},
    )
    data["adsb"]["sourceUrl"] = (
        os.getenv("adsbSourceUrl")
        or "http://192.168.1.74/readsb/data/aircraft.json"
    )
    data["adsb"]["userAgent"] = (
        os.getenv("adsbUserAgent")
        or "Mozilla/5.0 TrainDepartureDisplay/ADS-B"
    )
    data["adsb"]["fetchTimeout"] = _env_float(
        "adsbFetchTimeout",
        2.0,
        minimum=0.1,
    )
    data["adsb"]["refreshTime"] = _env_int("adsbRefreshTime", 10, minimum=1)
    data["adsb"]["displayCount"] = _env_int("adsbDisplayCount", 5, minimum=1)
    data["adsb"]["homeLat"] = _env_optional_float("adsbHomeLat")
    data["adsb"]["homeLon"] = _env_optional_float("adsbHomeLon")
    data["adsb"]["maxAgeSeconds"] = _env_float(
        "adsbMaxAgeSeconds",
        30.0,
        minimum=0.0,
    )
    data["adsb"]["maxDistanceNm"] = _env_optional_float("adsbMaxDistanceNm")
    data["adsb"]["minAltitudeFt"] = _env_optional_int("adsbMinAltitudeFt")
    data["adsb"]["routeLookupEnabled"] = _env_bool(
        "adsbRouteLookupEnabled",
        False,
    )
    data["adsb"]["routeApiUrl"] = (
        os.getenv("adsbRouteApiUrl")
        or "https://api.adsb.lol/api/0/routeset"
    )
    data["adsb"]["routeFetchTimeout"] = _env_float(
        "adsbRouteFetchTimeout",
        4.0,
        minimum=0.1,
    )
    data["adsb"]["routeDisplay"] = (
        os.getenv("adsbRouteDisplay") or "iata"
    ).lower()
    if data["adsb"]["routeDisplay"] not in ("iata", "icao", "city"):
        data["adsb"]["routeDisplay"] = "iata"

    data["adsb"]["topLeftTemplate"] = (
        os.getenv("adsbTopLeftTemplate") or DEFAULT_ADSB_TOP_LEFT_TEMPLATE
    )
    data["adsb"]["topRightTemplate"] = (
        os.getenv("adsbTopRightTemplate") or DEFAULT_ADSB_TOP_RIGHT_TEMPLATE
    )
    data["adsb"]["scrollTemplate"] = (
        os.getenv("adsbScrollTemplate") or DEFAULT_ADSB_SCROLL_TEMPLATE
    )
    data["adsb"]["nextLeftTemplate"] = (
        os.getenv("adsbNextLeftTemplate") or DEFAULT_ADSB_NEXT_LEFT_TEMPLATE
    )
    data["adsb"]["nextRightTemplate"] = (
        os.getenv("adsbNextRightTemplate") or DEFAULT_ADSB_NEXT_RIGHT_TEMPLATE
    )
    data["adsb"]["recordsStorePath"] = (
        os.getenv("adsbRecordsStorePath") or DEFAULT_ADSB_RECORDS_STORE_PATH
    )
    data["adsb"]["recordsWindows"] = normalize_record_windows(
        os.getenv("adsbRecordsWindows"),
    )

    data["planeAlert"]["enabled"] = _env_bool(
        "planeAlertEnabled",
        False,
    ) or _transport_mode_requested(
        raw_transport_modes,
        {"plane-alert", "planealert"},
    )
    data["planeAlert"]["sourceUrl"] = (
        os.getenv("planeAlertSourceUrl")
        or "http://192.168.1.74:8083/cgi/stream.sh?mode=plane-alert&date=all"
    )
    data["planeAlert"]["userAgent"] = (
        os.getenv("planeAlertUserAgent")
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    )
    data["planeAlert"]["fetchTimeout"] = _env_float(
        "planeAlertFetchTimeout",
        90.0,
        minimum=0.1,
    )
    data["planeAlert"]["refreshTime"] = _env_int(
        "planeAlertRefreshTime",
        30,
        minimum=1,
    )
    data["planeAlert"]["displayCount"] = _env_int(
        "planeAlertDisplayCount",
        MAX_PLANE_ALERT_DISPLAY_COUNT,
        minimum=1,
        maximum=MAX_PLANE_ALERT_DISPLAY_COUNT,
    )
    data["planeAlert"]["maxAgeHours"] = _env_optional_float(
        "planeAlertMaxAgeHours",
    )
    data["planeAlert"]["timeOffsetHours"] = _env_float(
        "planeAlertTimeOffset",
        0.0,
    )
    data["planeAlert"]["topLeftTemplate"] = (
        os.getenv("planeAlertTopLeftTemplate")
        or DEFAULT_PLANE_ALERT_TOP_LEFT_TEMPLATE
    )
    data["planeAlert"]["topRightTemplate"] = (
        os.getenv("planeAlertTopRightTemplate")
        or DEFAULT_PLANE_ALERT_TOP_RIGHT_TEMPLATE
    )
    data["planeAlert"]["scrollTemplate"] = (
        os.getenv("planeAlertScrollTemplate")
        or DEFAULT_PLANE_ALERT_SCROLL_TEMPLATE
    )
    data["planeAlert"]["nextLeftTemplate"] = (
        os.getenv("planeAlertNextLeftTemplate")
        or DEFAULT_PLANE_ALERT_NEXT_LEFT_TEMPLATE
    )
    data["planeAlert"]["nextRightTemplate"] = (
        os.getenv("planeAlertNextRightTemplate")
        or DEFAULT_PLANE_ALERT_NEXT_RIGHT_TEMPLATE
    )

    data["transport"]["modes"] = _default_transport_modes(
        raw_transport_modes,
        data["adsb"]["enabled"],
        data["planeAlert"]["enabled"],
    )
    data["transport"]["lastLineText"] = (
        os.getenv("lastLineText") or DEFAULT_LAST_LINE_TEXT
    )
    data["transport"]["modeSwitchInterval"] = _env_int(
        "modeSwitchInterval",
        300,
        minimum=1,
    )
    data["transport"]["modeRunCount"] = _env_optional_int(
        "modeRunCount",
        minimum=1,
    )
    data["transport"]["fallbackMode"] = (
        os.getenv("transportFallbackMode") or "train"
    ).lower()

    return data
