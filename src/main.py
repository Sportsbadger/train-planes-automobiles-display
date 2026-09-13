import os
import time
from pathlib import Path

import requests

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from PIL import ImageFont, Image, ImageDraw

from trains import loadDeparturesForStation
from adsb import (
    AdsbAircraft,
    AdsbDataError,
    AdsbRouteDataError,
    build_aircraft_template_text,
    enrich_aircraft_routes,
    fetch_aircraft_json,
    fetch_route_lookup_json,
    parse_aircraft,
    select_featured_aircraft_index,
    select_secondary_aircraft_display_rows,
)
from adsb_records import (
    AdsbRecordBoard,
    AdsbRecordStoreError,
    build_record_summary_text,
    filter_record_boards,
    format_record_line,
    load_adsb_record_boards,
    record_mode_entry_count,
    select_record_display_page,
    update_adsb_record_store,
)
from config import loadConfig
from plane_alert import (
    PlaneAlert,
    PlaneAlertDataError,
    build_plane_alert_template_text,
    fetch_plane_alert_json,
    parse_plane_alerts,
    select_featured_plane_alert_index,
    select_secondary_plane_alert_display_rows,
)
from open import isRun
from departure_loop import (
    advance_loop_index,
    build_loop_state,
    get_looped_departures,
    ordinal,
    timed_loop_index,
)
from transport_modes import (
    build_mode_state,
    mode_run_duration_s,
    parse_modes,
    aligned_mode_switch_interval_s,
    update_mode_state,
)
from refresh_cache import AsyncRefreshCache
from bitmap_cache import BitmapTextCache
from scroll_sync import (
    SCROLL_REQUIRED_CYCLES,
    ScrollCompletion,
    mode_scroll_required_cycles,
)

import RPi.GPIO as GPIO

from luma.core.interface.serial import spi, noop
from luma.core.render import canvas
from luma.oled.device import ssd1322
from luma.core.virtual import viewport, snapshot
from luma.core.sprite_system import framerate_regulator

import socket, re, uuid
from typing import Any, Callable

def makeFont(name, size):
    font_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            'fonts',
            name
        )
    )
    return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)


def renderDestination(departure, font, pos):
    departureTime = departure["aimed_departure_time"]
    destinationName = departure["destination_name"]

    def drawText(draw, *_):
        if config["showDepartureNumbers"]:
            train = f"{pos}  {departureTime}  {destinationName}"
        else:
            train = f"{departureTime}  {destinationName}"
        _, _, bitmap = cachedBitmapText(train, font)
        draw.bitmap((0, 0), bitmap, fill="yellow")

    return drawText


def renderServiceStatus(departure):
    def drawText(draw, width, *_):
        train = ""

        if departure["expected_departure_time"] == "On time":
            train = "On time"
        elif departure["expected_departure_time"] == "Cancelled":
            train = "Cancelled"
        elif departure["expected_departure_time"] == "Delayed":
            train = "Delayed"
        else:
            if isinstance(departure["expected_departure_time"], str):
                train = 'Exp ' + departure["expected_departure_time"]

            if departure["aimed_departure_time"] == departure["expected_departure_time"]:
                train = "On time"

        w, _, bitmap = cachedBitmapText(train, font)
        draw.bitmap((width - w, 0), bitmap, fill="yellow")
    return drawText


def renderPlatform(departure):
    def drawText(draw, *_):
        if "platform" in departure:
            platform = "Plat " + departure["platform"]
            if departure["platform"].lower() == "bus":
                platform = "BUS"
            _, _, bitmap = cachedBitmapText(platform, font)
            draw.bitmap((0, 0), bitmap, fill="yellow")
    return drawText


def renderCallingAt(draw, *_):
    stations = "Calling at: "
    _, _, bitmap = cachedBitmapText(stations, font)
    draw.bitmap((0, 0), bitmap, fill="yellow")


bitmapRenderCache = BitmapTextCache(max_entries=512)


def cachedBitmapText(text, font):
    return bitmapRenderCache.get(text, font)


pixelsLeft = 1
pixelsUp = 0
hasElevated = 0
pauseCount = 0
loopPixelsUp = 0
loopPauseCount = 0
loopHasElevated = 0
adsbLoopPixelsUp = 0
adsbLoopPauseCount = 0
adsbLoopHasElevated = 0
CACHE_REFRESH_CHECK_INTERVAL_S = 1.0
# ADS-B can spend up to six seconds in sequential aircraft and route requests.
# Leave additional time for parsing and record-store persistence before rendering.
PREFETCH_LEAD_TIME_S = 10.0
PLANE_ENTRY_SCROLL_MULTIPLIER = 2.0
STATIC_SNAPSHOT_INTERVAL_S = 60.0
SCROLL_SNAPSHOT_INTERVAL_S = 0.02
SCROLL_CYCLE_SAFETY_FRAMES = 5
SCROLL_EXIT_VIEWPORT_WIDTH = 256
SCROLL_SYNCED_MODES = {"adsb", "adsb-records", "plane-alert"}


def mode_entry_interval_s(
    mode: str,
    app_config: dict[str, Any],
    value: Any | None = None,
) -> float:
    """Return seconds to keep a mode entry active before advancing."""
    base_interval = max(1.0, float(app_config["loopDepartureInterval"]))
    if mode in {"adsb", "plane-alert"}:
        base_interval *= PLANE_ENTRY_SCROLL_MULTIPLIER

    return max(base_interval, max_mode_scroll_duration_s(mode, value, app_config))


def effective_scroll_frame_interval_s(app_config: dict[str, Any]) -> float:
    """Return the slowest expected frame interval for scroll animation."""
    target_fps = max(1.0, float(app_config.get("targetFPS") or 1.0))
    return max(SCROLL_SNAPSHOT_INTERVAL_S, 1.0 / target_fps)


def max_mode_scroll_duration_s(
    mode: str,
    value: Any | None,
    app_config: dict[str, Any],
) -> float:
    """Return the longest scroll animation duration needed by a mode."""
    scroll_texts = mode_scroll_texts(mode, value, app_config)
    if not scroll_texts:
        return 0.0
    return max(
        scroll_animation_duration_s(
            text,
            frame_interval_s=effective_scroll_frame_interval_s(app_config),
            cycles=mode_scroll_required_cycles(mode),
        )
        for text in scroll_texts
    )


def mode_scroll_texts(
    mode: str,
    value: Any | None,
    app_config: dict[str, Any],
) -> list[str]:
    """Return scroll-row texts that can be displayed for a transport mode."""
    if not isinstance(value, list):
        return []

    if mode == "adsb":
        aircraft = value[: app_config["adsb"]["displayCount"]]
        if not all(isinstance(item, AdsbAircraft) for item in aircraft):
            return []
        template = app_config["adsb"]["scrollTemplate"]
        return [
            build_aircraft_template_text(template, item, position)
            for position, item in enumerate(aircraft, start=1)
        ]

    if mode == "plane-alert":
        alerts = value[: app_config["planeAlert"]["displayCount"]]
        if not all(isinstance(item, PlaneAlert) for item in alerts):
            return []
        template = app_config["planeAlert"]["scrollTemplate"]
        return [
            build_plane_alert_template_text(template, alert, position)
            for position, alert in enumerate(alerts, start=1)
        ]

    if mode == "adsb-records":
        if not all(isinstance(item, AdsbRecordBoard) for item in value):
            return []
        return [build_record_summary_text(board) for board in value]

    return []


def scroll_animation_duration_s(
    text: str,
    *,
    initial_pause_frames: int = 50,
    final_pause_frames: int = 8,
    frame_interval_s: float = SCROLL_SNAPSHOT_INTERVAL_S,
    cycles: int = SCROLL_REQUIRED_CYCLES,
    viewport_width: int = SCROLL_EXIT_VIEWPORT_WIDTH,
) -> float:
    """Return seconds needed for ``renderStations`` to fully exit twice."""
    if not text:
        return 0.0

    text_width, text_height, _bitmap = cachedBitmapText(text, font)
    frames = (
        text_height
        + max(0, initial_pause_frames)
        + text_width
        + max(0, viewport_width)
        + max(0, final_pause_frames)
        + SCROLL_CYCLE_SAFETY_FRAMES
    )
    return frames * frame_interval_s * max(1, cycles)


def mode_entry_count(
    mode: str,
    value: Any,
    app_config: dict[str, Any],
) -> int:
    """Return the number of entries/pages needed for one full mode cycle."""
    if mode == "adsb" and isinstance(value, list):
        return max(1, min(len(value), int(app_config["adsb"]["displayCount"])))
    if mode == "plane-alert" and isinstance(value, list):
        return max(1, min(len(value), int(app_config["planeAlert"]["displayCount"])))
    if mode == "adsb-records" and isinstance(value, list):
        return record_mode_entry_count(value)
    if mode != "train" or not isinstance(value, tuple) or len(value) < 1:
        return 1

    departures = value[0]
    if not isinstance(departures, list):
        return 1
    loop_departure_count = int(app_config["loopDepartureCount"])
    visible_loop_departures = min(
        max(len(departures) - 1, 0),
        loop_departure_count,
    )
    return max(1, (visible_loop_departures + 1) // 2)


def renderStations(
    stations,
    initial_pause_frames=20,
    completion: ScrollCompletion | None = None,
):
    pixels_left = 1
    pixels_up = 0
    has_elevated = False
    pause_count = 0
    txt_width, txt_height, bitmap = cachedBitmapText(stations, font)

    def drawText(draw, *_):
        nonlocal pixels_left, pixels_up, has_elevated, pause_count

        if completion is not None and completion.complete:
            return

        if has_elevated:
            # slide the bitmap left until it's fully out of view
            draw.bitmap((pixels_left - 1, 0), bitmap, fill="yellow")
            if -pixels_left > txt_width:
                pause_count += 1
                if pause_count >= 8:
                    if completion is not None:
                        completion.mark_cycle_complete()
                        if completion.complete:
                            return
                    pixels_left = 1
                    pixels_up = 0
                    has_elevated = False
                    pause_count = 0
                return

            pause_count = 0
            pixels_left -= 1
            return

        # slide the bitmap up from the bottom of its viewport until fully visible
        draw.bitmap((0, txt_height - pixels_up), bitmap, fill="yellow")
        if pixels_up >= txt_height:
            pause_count += 1
            if pause_count > initial_pause_frames:
                has_elevated = True
                pixels_up = 0
                pause_count = 0
            return

        pixels_up += 1

    return drawText


def renderTime(draw, width, *_):
    rawTime = datetime.now().time()
    hour, minute, second = str(rawTime).split('.')[0].split(':')

    w1, _, HMBitmap = cachedBitmapText("{}:{}".format(hour, minute), fontBoldLarge)
    w2, _, _ = cachedBitmapText(':00', fontBoldTall)
    _, _, SBitmap = cachedBitmapText(':{}'.format(second), fontBoldTall)

    draw.bitmap(((width - w1 - w2) / 2, 0), HMBitmap, fill="yellow")
    draw.bitmap((((width - w1 - w2) / 2) + w1, 5), SBitmap, fill="yellow")


def renderTimeWithModeLabel(label: str) -> Callable[..., None]:
    """Render the clock row with a transport mode label on the left."""

    def drawText(draw: ImageDraw.ImageDraw, width: int, *_: Any) -> None:
        renderTime(draw, width)
        _, _, bitmap = cachedBitmapText(label, font)
        draw.bitmap((0, 3), bitmap, fill="yellow")

    return drawText


def renderDebugScreen(lines):
    def drawDebug(draw, *_):
        # draw a box
        draw.rectangle((1, 1, 254, 45), outline="yellow", fill=None)

        # coords for each line of text
        coords = {
            '1A': (5, 5),
            '1B': (45, 5),
            '2A': (5, 18),
            '2B': (45, 18),
            '3A': (5, 31),
            '3B': (45, 31),
            '3C': (140, 31)
        }

        # loop through lines and check if cached
        for key, text in lines.items():
            w, _, bitmap = cachedBitmapText(text, font)
            draw.bitmap(coords[key], bitmap, fill="yellow")        

    return drawDebug

def renderWelcomeTo(xOffset):
    def drawText(draw, *_):
        text = "Welcome to"
        draw.text((int(xOffset), 0), text=text, font=fontBold, fill="yellow")

    return drawText


def renderPoweredBy(xOffset):
    def drawText(draw, *_):
        text = "Powered by"
        draw.text((int(xOffset), 0), text=text, font=fontBold, fill="yellow")

    return drawText


def renderNRE(xOffset):
    def drawText(draw, *_):
        text = "National Rail Enquiries"
        draw.text((int(xOffset), 0), text=text, font=fontBold, fill="yellow")

    return drawText


def renderName(xOffset):
    def drawText(draw, *_):
        text = "UK Train Departure Display"
        draw.text((int(xOffset), 0), text=text, font=fontBold, fill="yellow")

    return drawText

def renderVersion(xOffset):
    def drawText(draw, *_):
        text = "v" + getVersionNumber().strip() + " " + getVersionDate()
        draw.text((int(xOffset), 0), text=text, font=font, fill="yellow")

    return drawText

def renderDepartureStation(departureStation, xOffset):
    def draw(draw, *_):
        text = departureStation
        draw.text((int(xOffset), 0), text=text, font=fontBold, fill="yellow")

    return draw


def renderDots(draw, *_):
    text = ".  .  ."
    draw.text((0, 0), text=text, font=fontBold, fill="yellow")


def loadData(apiConfig, journeyConfig, config):
    runHours = []
    if config['hoursPattern'].match(apiConfig['operatingHours']):
        runHours = [int(x) for x in apiConfig['operatingHours'].split('-')]

    if len(runHours) == 2 and isRun(runHours[0], runHours[1]) is False:
        return False, False, journeyConfig['outOfHoursName']

    # set rows to 10 (max allowed) to get as many departure as poss
    # leaving as a variable so this can be updated if the API does
    rows = "10"

    try:
        departures, stationName = loadDeparturesForStation(
            journeyConfig, apiConfig["apiKey"], rows)

        if departures is None:
            return False, False, stationName

        firstDepartureDestinations = departures[0]["calling_at_list"]
        return departures, firstDepartureDestinations, stationName
    except requests.RequestException as err:
        print("Error: Failed to fetch data from OpenLDBWS")
        print(err.__context__)
        return False, False, journeyConfig['outOfHoursName']



def loadPlaneAlertData(planeAlertConfig: dict[str, Any]):
    if not planeAlertConfig["enabled"]:
        return False

    try:
        payload = fetch_plane_alert_json(
            planeAlertConfig["sourceUrl"],
            float(planeAlertConfig["fetchTimeout"]),
            planeAlertConfig["userAgent"],
        )
        return parse_plane_alerts(
            payload,
            planeAlertConfig["maxAgeHours"],
            int(planeAlertConfig["displayCount"]),
            time_offset_hours=float(planeAlertConfig["timeOffsetHours"]),
        )
    except requests.Timeout as err:
        print("Error: Failed to fetch Plane-Alert data before timeout")
        print(
            "Increase planeAlertFetchTimeout if the Plane-Alert history "
            "query is slow, or narrow the timestamp regex. URL: "
            f"{planeAlertConfig['sourceUrl']}"
        )
        print(err)
        return False
    except requests.RequestException as err:
        print("Error: Failed to fetch Plane-Alert data")
        print(err)
        return False
    except PlaneAlertDataError as err:
        print(f"Error: Failed to parse Plane-Alert data: {err}")
        return False


def loadAdsbData(adsbConfig):
    if not adsbConfig["enabled"]:
        return False
    if adsbConfig["homeLat"] is None or adsbConfig["homeLon"] is None:
        print("Error: Please configure adsbHomeLat and adsbHomeLon")
        return False

    try:
        payload = fetch_aircraft_json(
            adsbConfig["sourceUrl"],
            float(adsbConfig["fetchTimeout"]),
            adsbConfig["userAgent"],
        )
        aircraft = parse_aircraft(
            payload,
            float(adsbConfig["homeLat"]),
            float(adsbConfig["homeLon"]),
            float(adsbConfig["maxAgeSeconds"]),
            adsbConfig["maxDistanceNm"],
            adsbConfig["minAltitudeFt"],
            int(adsbConfig["displayCount"]),
        )
        if adsbConfig["routeLookupEnabled"] and aircraft:
            try:
                route_payload = fetch_route_lookup_json(
                    adsbConfig["routeApiUrl"],
                    aircraft,
                    float(adsbConfig["routeFetchTimeout"]),
                    adsbConfig["userAgent"],
                )
                aircraft = enrich_aircraft_routes(
                    aircraft,
                    route_payload,
                    adsbConfig["routeDisplay"],
                )
            except requests.RequestException as err:
                print("Warning: Failed to fetch ADS-B route data")
                print(err)
            except AdsbRouteDataError as err:
                print(f"Warning: Failed to parse ADS-B route data: {err}")

        try:
            update_adsb_record_store(
                Path(adsbConfig["recordsStorePath"]),
                aircraft,
            )
        except AdsbRecordStoreError as err:
            print(f"Warning: Failed to update ADS-B records: {err}")
        return aircraft
    except requests.RequestException as err:
        print("Error: Failed to fetch ADS-B data")
        print(err)
        return False
    except AdsbDataError as err:
        print(f"Error: Failed to parse ADS-B data: {err}")
        return False


def loadAdsbRecordsData(adsbConfig: dict[str, Any]) -> list[AdsbRecordBoard] | bool:
    """Load persisted ADS-B record boards for display."""
    if not adsbConfig["enabled"]:
        return False
    try:
        boards = load_adsb_record_boards(Path(adsbConfig["recordsStorePath"]))
        return filter_record_boards(boards, adsbConfig["recordsWindows"])
    except AdsbRecordStoreError as err:
        print(f"Error: Failed to load ADS-B records: {err}")
        return False

def drawStartup(device, width, height):
    virtualViewport = viewport(device, width=width, height=height)

    with canvas(device):
        nameSize = int(fontBold.getlength("UK Train Departure Display"))
        versionSize = int(font.getlength("v" + getVersionNumber().strip() + " " + getVersionDate()))
        poweredSize = int(fontBold.getlength("Powered by"))
        NRESize = int(fontBold.getlength("National Rail Enquiries"))

        rowOne = snapshot(width, 10, renderName((width - nameSize) / 2), interval=10)
        rowTwo = snapshot(width, 10, renderVersion((width - versionSize) / 2), interval=10)
        rowThree = snapshot(width, 10, renderPoweredBy((width - poweredSize) / 2), interval=10)
        rowFour = snapshot(width, 10, renderNRE((width - NRESize) / 2), interval=10)

        if len(virtualViewport._hotspots) > 0:
            for hotspot, xy in virtualViewport._hotspots:
                virtualViewport.remove_hotspot(hotspot, xy)

        virtualViewport.add_hotspot(rowOne, (0, 0))
        virtualViewport.add_hotspot(rowTwo, (0, 12))
        virtualViewport.add_hotspot(rowThree, (0, 24))
        virtualViewport.add_hotspot(rowFour, (0, 36))

    return virtualViewport

def drawDebugScreen(device, width, height, screen="1", showTime=False):
    virtualViewport = viewport(device, width=width, height=height)

    versionNumber = getVersionNumber().strip()
    
    ipAddress = getIp()

    macAddress = ':'.join(re.findall('..', '%012x' % uuid.getnode())).upper()

    debugLines = {}

    # ok let's build the strings, there's a bit of optional data here so let's do it the old fashioned way with appends

    debugLines["1A"] = "Display"

    debugLines["1B"] = f"= {config['journey']['departureStation']}"

    # has a destination been set? add it in!
    if(config["journey"]["destinationStation"]):
        debugLines["1B"] += f"->{config['journey']['destinationStation']}"

    # what about a plaform?
    if(config["journey"]["screen"+screen+"Platform"]):
        debugLines["1B"] += f" (Plat{config['journey']['screen'+screen+'Platform']}) "
    else:
        debugLines["1B"] += " (PlatAll) "

    # refresh time
    debugLines["1B"] += f"{config['refreshTime']}s "
    
    # this wasn't set on my default so will wrap it in if, just in case
    if(config['api']['operatingHours']):
        debugLines["1B"] += f"{config['api']['operatingHours']}h"
    
    debugLines["2A"] = "Script"
    debugLines["2B"] = f"= T_D_D:  {versionNumber}"

    debugLines["3A"] = "Address"
    debugLines["3B"] = f"= {macAddress}"
    debugLines["3C"] = f"IP={ipAddress}"

    theBox = snapshot(width, 64, renderDebugScreen(debugLines), interval=config["refreshTime"])
    virtualViewport.add_hotspot(theBox, (0, 0))

    if(showTime):
        rowTime = snapshot(
        width,
        14,
        renderTimeWithModeLabel("ADSB"),
        interval=0.1,
    )
        virtualViewport.add_hotspot(rowTime, (0, 50))

    return virtualViewport




def drawBlankSignage(device, width, height, departureStation):
    global stationRenderCount, pauseCount, loopPixelsUp, loopPauseCount, loopHasElevated

    welcomeSize = int(fontBold.getlength("Welcome to"))
    stationSize = int(fontBold.getlength(departureStation))

    device.clear()

    virtualViewport = viewport(device, width=width, height=height)

    rowOne = snapshot(width, 10, renderWelcomeTo(
        (width - welcomeSize) / 2), interval=config["refreshTime"])
    rowTwo = snapshot(width, 10, renderDepartureStation(
        departureStation, (width - stationSize) / 2), interval=config["refreshTime"])
    rowThree = snapshot(width, 10, renderDots, interval=config["refreshTime"])
    # this will skip a second sometimes if set to 1, but a hotspot burns CPU
    # so set to snapshot of 0.1; you won't notice
    rowTime = snapshot(width, 14, renderTime, interval=0.1)

    if len(virtualViewport._hotspots) > 0:
        for vhotspot, xy in virtualViewport._hotspots:
            virtualViewport.remove_hotspot(vhotspot, xy)

    virtualViewport.add_hotspot(rowOne, (0, 0))
    virtualViewport.add_hotspot(rowTwo, (0, 12))
    virtualViewport.add_hotspot(rowThree, (0, 24))
    virtualViewport.add_hotspot(rowTime, (0, 50))

    return virtualViewport


def platform_filter(departureData, platformNumber, station):
    platformDepartures = []
    for sub in departureData:
        if platformNumber == "":
            platformDepartures.append(sub)
        elif sub.get('platform') is not None:
            if sub['platform'] == platformNumber:
                res = sub
                platformDepartures.append(res)

    if len(platformDepartures) > 0:
        firstDepartureDestinations = platformDepartures[0]["calling_at_list"]
        platformData = platformDepartures, firstDepartureDestinations, station
    else:
        platformData = platformDepartures, "", station

    return platformData


def drawSignage(device, width, height, data):
    global stationRenderCount, pauseCount

    virtualViewport = viewport(device, width=width, height=height)

    status = "Exp 00:00"
    callingAt = "Calling at: "
    platform = "Plat 888"

    departures, firstDepartureDestinations, departureStation = data

    w = int(font.getlength(callingAt))

    callingWidth = w
    width = virtualViewport.width

    # First measure the text size
    w = int(font.getlength(status))
    pw = int(font.getlength(platform))

    if len(departures) == 0:
        noTrains = drawBlankSignage(device, width=width, height=height, departureStation=departureStation)
        return noTrains

    firstFont = font
    if config['firstDepartureBold']:
        firstFont = fontBold

    rowOneA = snapshot(
        width - w - pw - 5, 10, renderDestination(departures[0], firstFont, '1st'), interval=config["refreshTime"])
    rowOneB = snapshot(w, 10, renderServiceStatus(
        departures[0]), interval=10)
    rowOneC = snapshot(pw, 10, renderPlatform(departures[0]), interval=config["refreshTime"])
    rowTwoA = snapshot(callingWidth, 10, renderCallingAt, interval=config["refreshTime"])
    rowTwoB = snapshot(width - callingWidth, 10,
                       renderStations(firstDepartureDestinations), interval=0.02)

    loop_state = build_loop_state(
        departures,
        config["loopDepartureCount"],
        time.monotonic(),
    )

    loop_row_gap = 12
    loop_block_height = loop_row_gap * 2
    loop_frame_interval = 0.02

    def get_loop_render_state() -> tuple[list[tuple[int, dict[str, str]]], list[tuple[int, dict[str, str]]], int]:
        global loopPixelsUp, loopPauseCount, loopHasElevated

        current = get_looped_departures(loop_state.departures, loop_state.index)
        next_index = advance_loop_index(loop_state.index, len(loop_state.departures))
        upcoming = get_looped_departures(loop_state.departures, next_index)

        interval_s = float(config["loopDepartureInterval"])
        total_frames = max(loop_block_height, int(interval_s / loop_frame_interval))
        pause_frames = max(0, total_frames - loop_block_height)

        if loopHasElevated:
            loopPixelsUp += 1
            if loopPixelsUp >= loop_block_height:
                loop_state.index = next_index
                loopPixelsUp = 0
                loopHasElevated = 0
                loopPauseCount = 0
        else:
            loopPauseCount += 1
            if loopPauseCount >= pause_frames:
                loopHasElevated = 1
                loopPauseCount = 0

        return current, upcoming, loopPixelsUp

    def draw_loop_destination(
        draw: ImageDraw.ImageDraw,
        y_offset: int,
        departure: dict[str, str],
        position: int,
        *_: Any,
    ) -> None:
        if config["showDepartureNumbers"]:
            train = f"{ordinal(position)}  {departure['aimed_departure_time']}  {departure['destination_name']}"
        else:
            train = f"{departure['aimed_departure_time']}  {departure['destination_name']}"
        _, _, bitmap = cachedBitmapText(train, font)
        draw.bitmap((0, y_offset), bitmap, fill="yellow")

    def draw_loop_status(
        draw: ImageDraw.ImageDraw,
        y_offset: int,
        departure: dict[str, str],
        _position: int,
        width: int,
    ) -> None:
        train = ""
        if departure["expected_departure_time"] == "On time":
            train = "On time"
        elif departure["expected_departure_time"] == "Cancelled":
            train = "Cancelled"
        elif departure["expected_departure_time"] == "Delayed":
            train = "Delayed"
        else:
            if isinstance(departure["expected_departure_time"], str):
                train = "Exp " + departure["expected_departure_time"]
            if departure["aimed_departure_time"] == departure["expected_departure_time"]:
                train = "On time"
        text_width, _, bitmap = cachedBitmapText(train, font)
        draw.bitmap((width - text_width, y_offset), bitmap, fill="yellow")

    def draw_loop_platform(
        draw: ImageDraw.ImageDraw,
        y_offset: int,
        departure: dict[str, str],
        _position: int,
        *_: Any,
    ) -> None:
        if "platform" not in departure:
            return
        platform = "Plat " + departure["platform"]
        if departure["platform"].lower() == "bus":
            platform = "BUS"
        _, _, bitmap = cachedBitmapText(platform, font)
        draw.bitmap((0, y_offset), bitmap, fill="yellow")

    def render_loop_block(
        renderer: Callable[[ImageDraw.ImageDraw, int, dict[str, str], int, int], None],
    ) -> Callable[..., None]:
        def drawText(draw: ImageDraw.ImageDraw, width: int, *_: Any) -> None:
            current, upcoming, pixel_offset = get_loop_render_state()
            current_offset = -pixel_offset
            next_offset = loop_block_height + current_offset
            for idx, (position, departure) in enumerate(current):
                renderer(draw, current_offset + (idx * loop_row_gap), departure, position, width)
            for idx, (position, departure) in enumerate(upcoming):
                renderer(draw, next_offset + (idx * loop_row_gap), departure, position, width)

        return drawText

    if len(loop_state.departures) > 0:
        rowThreeA = snapshot(
            width - w - pw,
            loop_block_height,
            render_loop_block(draw_loop_destination),
            interval=loop_frame_interval,
        )
        rowThreeB = snapshot(
            w,
            loop_block_height,
            render_loop_block(draw_loop_status),
            interval=loop_frame_interval,
        )
        rowThreeC = snapshot(
            pw,
            loop_block_height,
            render_loop_block(draw_loop_platform),
            interval=loop_frame_interval,
        )

    rowTime = snapshot(width, 14, renderTime, interval=0.1)

    if len(virtualViewport._hotspots) > 0:
        for vhotspot, xy in virtualViewport._hotspots:
            virtualViewport.remove_hotspot(vhotspot, xy)

    stationRenderCount = 0
    pauseCount = 0
    loopPixelsUp = 0
    loopPauseCount = 0
    loopHasElevated = 0

    virtualViewport.add_hotspot(rowOneA, (0, 0))
    virtualViewport.add_hotspot(rowOneB, (width - w, 0))
    virtualViewport.add_hotspot(rowOneC, (width - w - pw, 0))
    virtualViewport.add_hotspot(rowTwoA, (0, 12))
    virtualViewport.add_hotspot(rowTwoB, (callingWidth, 12))

    if len(loop_state.departures) > 0:
        virtualViewport.add_hotspot(rowThreeA, (0, 24))
        virtualViewport.add_hotspot(rowThreeB, (width - w, 24))
        virtualViewport.add_hotspot(rowThreeC, (width - w - pw, 24))

    virtualViewport.add_hotspot(rowTime, (0, 50))

    return virtualViewport


def renderAdsbSummary(left_text: str, right_text: str, font):
    def drawText(draw, width, *_):
        _, _, left_bitmap = cachedBitmapText(left_text, font)
        right_width, _, right_bitmap = cachedBitmapText(right_text, font)

        draw.bitmap((0, 0), left_bitmap, fill="yellow")
        if right_text:
            draw.bitmap(
                (max(0, width - right_width), 0),
                right_bitmap,
                fill="yellow",
            )

    return drawText


def renderAdsbBearing(aircraft):
    def drawText(draw, width, *_):
        bearing = f"{aircraft.bearing_deg:03d}deg"
        text_width, _, bitmap = cachedBitmapText(bearing, font)
        draw.bitmap((width - text_width, 0), bitmap, fill="yellow")

    return drawText


def renderTrackingLabel(draw, *_):
    label = "Tracking: "
    _, _, bitmap = cachedBitmapText(label, font)
    draw.bitmap((0, 0), bitmap, fill="yellow")


def drawAdsbSignage(
    device: Any,
    width: int,
    height: int,
    aircraft: list[Any],
    mode_elapsed_s: float | None = None,
    entry_index: int | None = None,
    scroll_completion: ScrollCompletion | None = None,
) -> Any:
    global stationRenderCount, pauseCount
    global adsbLoopPixelsUp, adsbLoopPauseCount, adsbLoopHasElevated

    if len(aircraft) == 0:
        return drawBlankSignage(
            device,
            width=width,
            height=height,
            departureStation="No aircraft",
        )

    virtualViewport = viewport(device, width=width, height=height)
    width = virtualViewport.width
    firstFont = fontBold if config['firstDepartureBold'] else font

    top_left_template = config["adsb"]["topLeftTemplate"]
    top_right_template = config["adsb"]["topRightTemplate"]
    scroll_template = config["adsb"]["scrollTemplate"]
    next_left_template = config["adsb"]["nextLeftTemplate"]
    next_right_template = config["adsb"]["nextRightTemplate"]
    loop_row_gap = 12
    loop_block_height = loop_row_gap * 2
    loop_frame_interval = SCROLL_SNAPSHOT_INTERVAL_S

    display_aircraft = aircraft[: config["adsb"]["displayCount"]]
    if entry_index is None:
        featured_index = select_featured_aircraft_index(
            display_aircraft,
            mode_elapsed_s if mode_elapsed_s is not None else time.monotonic(),
            mode_entry_interval_s("adsb", config, aircraft),
        )
    else:
        featured_index = entry_index % len(display_aircraft)
    featured_aircraft = display_aircraft[featured_index]
    featured_position = featured_index + 1
    top_left_text = build_aircraft_template_text(
        top_left_template,
        featured_aircraft,
        featured_position,
    )
    top_right_text = build_aircraft_template_text(
        top_right_template,
        featured_aircraft,
        featured_position,
    )
    scroll_text = build_aircraft_template_text(
        scroll_template,
        featured_aircraft,
        featured_position,
    )

    rowOneA = snapshot(
        width,
        10,
        renderAdsbSummary(top_left_text, top_right_text, firstFont),
        interval=STATIC_SNAPSHOT_INTERVAL_S,
    )
    rowTwoB = snapshot(
        width,
        10,
        renderStations(
            scroll_text,
            initial_pause_frames=50,
            completion=scroll_completion,
        ),
        interval=loop_frame_interval,
    )

    loop_departures = select_secondary_aircraft_display_rows(
        display_aircraft,
        featured_index,
    )

    loop_display_rows = [
        (
            position,
            plane,
            (
                config["transport"]["lastLineText"]
                if plane is None
                else build_aircraft_template_text(
                    next_left_template,
                    plane,
                    position,
                )
            ),
            "" if plane is None else build_aircraft_template_text(
                next_right_template,
                plane,
                position,
            ),
        )
        for position, plane in loop_departures
    ]

    def get_adsb_loop_render_state():
        return loop_display_rows

    def render_adsb_loop_block() -> Callable[..., None]:
        def drawText(draw: ImageDraw.ImageDraw, width: int, *_: Any) -> None:
            current = get_adsb_loop_render_state()
            for idx, (_position, plane, left_text, right_text) in enumerate(current):
                y_offset = idx * loop_row_gap
                left_width, _, left_bitmap = cachedBitmapText(left_text, font)
                if plane is None:
                    draw.bitmap(
                        (max(0, (width - left_width) / 2), y_offset),
                        left_bitmap,
                        fill="yellow",
                    )
                    continue

                draw.bitmap((0, y_offset), left_bitmap, fill="yellow")
                if not right_text:
                    continue
                right_width, _, right_bitmap = cachedBitmapText(right_text, font)
                draw.bitmap(
                    (max(0, width - right_width), y_offset),
                    right_bitmap,
                    fill="yellow",
                )

        return drawText

    if len(loop_display_rows) > 0:
        rowThree = snapshot(
            width,
            loop_block_height,
            render_adsb_loop_block(),
            interval=STATIC_SNAPSHOT_INTERVAL_S,
        )
    rowTime = snapshot(
        width,
        14,
        renderTimeWithModeLabel("ADSB"),
        interval=0.1,
    )

    if len(virtualViewport._hotspots) > 0:
        for vhotspot, xy in virtualViewport._hotspots:
            virtualViewport.remove_hotspot(vhotspot, xy)

    stationRenderCount = 0
    pauseCount = 0
    adsbLoopPixelsUp = 0
    adsbLoopPauseCount = 0
    adsbLoopHasElevated = 0

    virtualViewport.add_hotspot(rowOneA, (0, 0))
    virtualViewport.add_hotspot(rowTwoB, (0, 12))

    if len(loop_display_rows) > 0:
        virtualViewport.add_hotspot(rowThree, (0, 24))

    virtualViewport.add_hotspot(rowTime, (0, 50))

    return virtualViewport


def drawAdsbRecordsSignage(
    device: Any,
    width: int,
    height: int,
    boards: list[AdsbRecordBoard],
    mode_elapsed_s: float | None = None,
    entry_index: int | None = None,
    scroll_completion: ScrollCompletion | None = None,
) -> Any:
    """Build ADS-B record signage for day, week, and all-time boards."""
    if len(boards) == 0:
        return drawBlankSignage(
            device,
            width=width,
            height=height,
            departureStation="No ADS-B records",
        )

    virtualViewport = viewport(device, width=width, height=height)
    width = virtualViewport.width
    firstFont = fontBold if config['firstDepartureBold'] else font
    loop_row_gap = 12
    loop_block_height = loop_row_gap * 2
    loop_frame_interval = SCROLL_SNAPSHOT_INTERVAL_S

    entry_interval = mode_entry_interval_s("adsb-records", config, boards)
    page_elapsed_s = (
        entry_index * entry_interval
        if entry_index is not None
        else mode_elapsed_s if mode_elapsed_s is not None else time.monotonic()
    )
    board, record_rows = select_record_display_page(
        boards,
        page_elapsed_s,
        entry_interval,
    )
    if board is None:
        return drawBlankSignage(
            device,
            width=width,
            height=height,
            departureStation="No ADS-B records",
        )

    top_left_text = board.title
    top_right_text = f"{board.observation_count} hits"
    scroll_text = build_record_summary_text(board)

    rowOne = snapshot(
        width,
        10,
        renderAdsbSummary(top_left_text, top_right_text, firstFont),
        interval=STATIC_SNAPSHOT_INTERVAL_S,
    )
    rowTwo = snapshot(
        width,
        10,
        renderStations(
            scroll_text,
            initial_pause_frames=50,
            completion=scroll_completion,
        ),
        interval=loop_frame_interval,
    )

    def render_records_block() -> Callable[..., None]:
        def drawText(draw: ImageDraw.ImageDraw, width: int, *_: Any) -> None:
            if not record_rows:
                text_width, _, bitmap = cachedBitmapText(
                    "Collecting first records",
                    font,
                )
                draw.bitmap(
                    (max(0, (width - text_width) / 2), 0),
                    bitmap,
                    fill="yellow",
                )
                return
            for idx, record in enumerate(record_rows):
                y_offset = idx * loop_row_gap
                left_text = format_record_line(record)
                right_text = record.aircraft_type
                _, _, left_bitmap = cachedBitmapText(left_text, font)
                draw.bitmap((0, y_offset), left_bitmap, fill="yellow")
                if not right_text:
                    continue
                right_width, _, right_bitmap = cachedBitmapText(right_text, font)
                draw.bitmap(
                    (max(0, width - right_width), y_offset),
                    right_bitmap,
                    fill="yellow",
                )

        return drawText

    rowThree = snapshot(
        width,
        loop_block_height,
        render_records_block(),
        interval=STATIC_SNAPSHOT_INTERVAL_S,
    )
    rowTime = snapshot(
        width,
        14,
        renderTimeWithModeLabel("Statistics"),
        interval=0.1,
    )

    if len(virtualViewport._hotspots) > 0:
        for vhotspot, xy in virtualViewport._hotspots:
            virtualViewport.remove_hotspot(vhotspot, xy)

    virtualViewport.add_hotspot(rowOne, (0, 0))
    virtualViewport.add_hotspot(rowTwo, (0, 12))
    virtualViewport.add_hotspot(rowThree, (0, 24))
    virtualViewport.add_hotspot(rowTime, (0, 50))

    return virtualViewport


def renderTemplateSummary(
    left_text: str,
    right_text: str,
    display_font: Any,
) -> Callable[..., None]:
    def drawText(draw: ImageDraw.ImageDraw, width: int, *_: Any) -> None:
        _, _, left_bitmap = cachedBitmapText(left_text, display_font)
        right_width, _, right_bitmap = cachedBitmapText(right_text, font)
        draw.bitmap((0, 0), left_bitmap, fill="yellow")
        if right_text:
            draw.bitmap(
                (max(0, width - right_width), 0),
                right_bitmap,
                fill="yellow",
            )

    return drawText


def drawPlaneAlertSignage(
    device: Any,
    width: int,
    height: int,
    alerts: list[Any],
    mode_elapsed_s: float | None = None,
    entry_index: int | None = None,
    scroll_completion: ScrollCompletion | None = None,
) -> Any:
    global stationRenderCount, pauseCount

    if len(alerts) == 0:
        return drawBlankSignage(
            device,
            width=width,
            height=height,
            departureStation="No alerts",
        )

    virtualViewport = viewport(device, width=width, height=height)
    width = virtualViewport.width
    firstFont = fontBold if config['firstDepartureBold'] else font

    display_alerts = alerts[: config["planeAlert"]["displayCount"]]
    top_left_template = config["planeAlert"]["topLeftTemplate"]
    top_right_template = config["planeAlert"]["topRightTemplate"]
    scroll_template = config["planeAlert"]["scrollTemplate"]
    next_left_template = config["planeAlert"]["nextLeftTemplate"]
    next_right_template = config["planeAlert"]["nextRightTemplate"]
    loop_row_gap = 12
    loop_block_height = loop_row_gap * 2
    loop_frame_interval = SCROLL_SNAPSHOT_INTERVAL_S

    if entry_index is None:
        featured_index = select_featured_plane_alert_index(
            display_alerts,
            mode_elapsed_s if mode_elapsed_s is not None else time.monotonic(),
            mode_entry_interval_s("plane-alert", config, display_alerts),
        )
    else:
        featured_index = entry_index % len(display_alerts)
    featured_alert = display_alerts[featured_index]
    featured_position = featured_index + 1
    top_left_text = build_plane_alert_template_text(
        top_left_template,
        featured_alert,
        featured_position,
    )
    top_right_text = build_plane_alert_template_text(
        top_right_template,
        featured_alert,
        featured_position,
    )
    scroll_text = build_plane_alert_template_text(
        scroll_template,
        featured_alert,
        featured_position,
    )

    rowOneA = snapshot(
        width,
        10,
        renderAdsbSummary(top_left_text, top_right_text, firstFont),
        interval=STATIC_SNAPSHOT_INTERVAL_S,
    )
    rowTwoB = snapshot(
        width,
        10,
        renderStations(
            scroll_text,
            initial_pause_frames=50,
            completion=scroll_completion,
        ),
        interval=loop_frame_interval,
    )

    loop_departures = select_secondary_plane_alert_display_rows(
        display_alerts,
        featured_index,
    )

    loop_display_rows = [
        (
            position,
            alert,
            (
                config["transport"]["lastLineText"]
                if alert is None
                else build_plane_alert_template_text(
                    next_left_template,
                    alert,
                    position,
                )
            ),
            "" if alert is None else build_plane_alert_template_text(
                next_right_template,
                alert,
                position,
            ),
        )
        for position, alert in loop_departures
    ]

    def get_plane_alert_loop_render_state() -> list[
        tuple[int | None, Any | None, str, str]
    ]:
        return loop_display_rows

    def render_plane_alert_loop_block() -> Callable[..., None]:
        def drawText(draw: ImageDraw.ImageDraw, width: int, *_: Any) -> None:
            current = get_plane_alert_loop_render_state()
            for idx, (_position, alert, left_text, right_text) in enumerate(current):
                y_offset = idx * loop_row_gap
                left_width, _, left_bitmap = cachedBitmapText(left_text, font)
                if alert is None:
                    draw.bitmap(
                        (max(0, (width - left_width) / 2), y_offset),
                        left_bitmap,
                        fill="yellow",
                    )
                    continue

                draw.bitmap((0, y_offset), left_bitmap, fill="yellow")
                if not right_text:
                    continue
                right_width, _, right_bitmap = cachedBitmapText(right_text, font)
                draw.bitmap(
                    (max(0, width - right_width), y_offset),
                    right_bitmap,
                    fill="yellow",
                )

        return drawText

    if len(loop_display_rows) > 0:
        rowThree = snapshot(
            width,
            loop_block_height,
            render_plane_alert_loop_block(),
            interval=STATIC_SNAPSHOT_INTERVAL_S,
        )

    rowTime = snapshot(
        width,
        14,
        renderTimeWithModeLabel("Watchlist"),
        interval=0.1,
    )

    if len(virtualViewport._hotspots) > 0:
        for vhotspot, xy in virtualViewport._hotspots:
            virtualViewport.remove_hotspot(vhotspot, xy)

    stationRenderCount = 0
    pauseCount = 0

    virtualViewport.add_hotspot(rowOneA, (0, 0))
    virtualViewport.add_hotspot(rowTwoB, (0, 12))

    if len(loop_display_rows) > 0:
        virtualViewport.add_hotspot(rowThree, (0, 24))

    virtualViewport.add_hotspot(rowTime, (0, 50))

    return virtualViewport


def getIp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # doesn't even have to be reachable
        s.connect(('10.254.254.254', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def getVersionNumber():
    version_file = open('VERSION', 'r')
    return version_file.read()

def getVersionDate():
    modification_timestamp = os.path.getmtime('VERSION')

    # Convert the timestamp to a readable datetime object
    return datetime.fromtimestamp(modification_timestamp).strftime('%d %b %Y')


def next_transport_mode(modes: list[str], active_mode: str) -> str | None:
    """Return the mode that follows the active transport mode."""
    if len(modes) < 2 or active_mode not in modes:
        return None
    active_index = modes.index(active_mode)
    return modes[(active_index + 1) % len(modes)]


def active_mode_limit_s(
    mode: str,
    value: Any,
    app_config: dict[str, Any],
) -> float:
    """Return seconds before the active transport mode will switch."""
    entry_interval = mode_entry_interval_s(mode, app_config, value)
    mode_run_count = app_config["transport"].get("modeRunCount")
    if mode_run_count is None:
        switch_interval = float(app_config["transport"]["modeSwitchInterval"])
        return aligned_mode_switch_interval_s(switch_interval, entry_interval)
    return mode_run_duration_s(
        mode,
        mode_entry_count(mode, value, app_config),
        entry_interval,
        int(mode_run_count),
    )


def prefetch_modes_for_next_cycle(
    caches: dict[str, AsyncRefreshCache[Any]],
    modes: list[str],
    active_mode: str,
    now: float,
) -> None:
    """Start refreshes for the next mode before it is displayed."""
    next_mode = next_transport_mode(modes, active_mode)
    if next_mode is None or next_mode not in caches:
        return

    next_cache = caches[next_mode]
    # Refresh early when the value will become stale before the mode switch.
    prefetch_maximum_age_s = max(
        0.0,
        next_cache.refresh_interval_s - PREFETCH_LEAD_TIME_S,
    )
    next_cache.refresh_if_stale(now, prefetch_maximum_age_s)


def draw_cached_train_signage(
    primary_device: Any,
    secondary_device: Any,
    width: int,
    height: int,
    train_data: Any,
) -> tuple[Any, Any]:
    """Build train viewports from cached train data without network I/O."""
    if train_data is None:
        primary = drawBlankSignage(
            primary_device,
            width=width,
            height=height,
            departureStation="Loading trains",
        )
        secondary = None
        if config['dualScreen']:
            secondary = drawBlankSignage(
                secondary_device,
                width=width,
                height=height,
                departureStation="Loading trains",
            )
        return primary, secondary

    if train_data[0] is False:
        primary = drawBlankSignage(
            primary_device,
            width=width,
            height=height,
            departureStation=train_data[2],
        )
        secondary = None
        if config['dualScreen']:
            secondary = drawBlankSignage(
                secondary_device,
                width=width,
                height=height,
                departureStation=train_data[2],
            )
        return primary, secondary

    departure_data = train_data[0]
    station = train_data[2]
    screen_data = platform_filter(
        departure_data,
        config["journey"]["screen1Platform"],
        station,
    )
    primary = drawSignage(
        primary_device,
        width=width,
        height=height,
        data=screen_data,
    )
    secondary = None
    if config['dualScreen']:
        screen2_data = platform_filter(
            departure_data,
            config["journey"]["screen2Platform"],
            station,
        )
        secondary = drawSignage(
            secondary_device,
            width=width,
            height=height,
            data=screen2_data,
        )
    return primary, secondary

try:
    print('Starting Train Departure Display v' + getVersionNumber())
    config = loadConfig()
    if config['headless']:
        print('Headless mode, running main loop without serial comms')
        serial = noop()
    else:
        GPIO.setwarnings(False)
        serial = spi(port=0)
        
    device = ssd1322(serial, mode="1", rotate=config['screenRotation'])

    if config['dualScreen']:
        serial1 = spi(port=1, gpio_DC=5, gpio_RST=6)
        device1 = ssd1322(serial1, mode="1", rotate=config['screenRotation'])
    font = makeFont("Dot Matrix Regular.ttf", 10)
    fontBold = makeFont("Dot Matrix Bold.ttf", 10)
    fontBoldTall = makeFont("Dot Matrix Bold Tall.ttf", 10)
    fontBoldLarge = makeFont("Dot Matrix Bold.ttf", 20)

    widgetWidth = 256
    widgetHeight = 64

    stationRenderCount = 0
    pauseCount = 0
    loop_count = 0

    regulator = framerate_regulator(config['targetFPS'])
    transportModes = parse_modes(
        config["transport"]["modes"],
        config["adsb"]["enabled"],
        config["planeAlert"]["enabled"],
    )
    modeState = build_mode_state(transportModes, time.monotonic())
    refreshExecutor = ThreadPoolExecutor(max_workers=4)
    displayCaches: dict[str, AsyncRefreshCache[Any]] = {
        "train": AsyncRefreshCache(
            lambda: loadData(config["api"], config["journey"], config),
            float(config["refreshTime"]),
            refreshExecutor,
        ),
    }
    if config["adsb"]["enabled"]:
        displayCaches["adsb"] = AsyncRefreshCache(
            lambda: loadAdsbData(config["adsb"]),
            float(config["adsb"]["refreshTime"]),
            refreshExecutor,
        )
        displayCaches["adsb-records"] = AsyncRefreshCache(
            lambda: loadAdsbRecordsData(config["adsb"]),
            float(config["adsb"]["refreshTime"]),
            refreshExecutor,
        )
    if config["planeAlert"]["enabled"]:
        displayCaches["plane-alert"] = AsyncRefreshCache(
            lambda: loadPlaneAlertData(config["planeAlert"]),
            float(config["planeAlert"]["refreshTime"]),
            refreshExecutor,
        )

    initial_refresh_modes = {
        modeState.active_mode,
        config["transport"]["fallbackMode"],
    }
    if "adsb-records" in initial_refresh_modes:
        initial_refresh_modes.add("adsb")
    for cache_mode in initial_refresh_modes:
        if cache_mode in displayCaches:
            displayCaches[cache_mode].refresh_if_due(time.monotonic(), force=True)

    if (config['debug'] > 1):
        # render screen and sleep for specified seconds
        virtual = drawDebugScreen(device, width=widgetWidth, height=widgetHeight)
        virtual.refresh()
        if config['dualScreen']:
            virtual1 = drawDebugScreen(device1, width=widgetWidth, height=widgetHeight, screen="2")
            virtual1.refresh()
        time.sleep(config['debug'])
    else:
        # display NRE attribution while data loads
        virtual = drawStartup(device, width=widgetWidth, height=widgetHeight)
        virtual.refresh()
        if config['dualScreen']:
            virtual1 = drawStartup(device1, width=widgetWidth, height=widgetHeight)
            virtual1.refresh()
        if config['headless'] is not True:
            time.sleep(5)

    timeAtStart = 0
    timeNow = time.time()
    timeFPS = time.time()
    lastCacheRefreshCheck = 0.0
    prefetchedForModeSwitch: str | None = None
    activeScrollCompletion: ScrollCompletion | None = None
    syncedEntryIndex = 0

    blankHours = []
    if config['hoursPattern'].match(config['screenBlankHours']):
        blankHours = [int(x) for x in config['screenBlankHours'].split('-')]

    while True:
        with regulator:
            if len(blankHours) == 2 and isRun(blankHours[0], blankHours[1]):
                device.clear()
                if config['dualScreen']:
                    device1.clear()
                time.sleep(10)
            else:
                if timeNow - timeFPS >= config['fpsTime']:
                    timeFPS = time.time()
                    print('Effective FPS: ' + str(round(regulator.effective_FPS(), 2)))
                previousMode = modeState.active_mode
                now_monotonic = time.monotonic()
                active_snapshot = None
                if modeState.active_mode in displayCaches:
                    active_snapshot = displayCaches[modeState.active_mode].snapshot(
                        now_monotonic,
                    ).value
                can_switch_mode = not (
                    modeState.active_mode in SCROLL_SYNCED_MODES
                    and activeScrollCompletion is not None
                    and not activeScrollCompletion.complete
                )
                if can_switch_mode:
                    update_mode_state(
                        modeState,
                        transportModes,
                        now_monotonic,
                        float(config["transport"]["modeSwitchInterval"]),
                        mode_run_count=config["transport"].get("modeRunCount"),
                        entry_count=mode_entry_count(
                            modeState.active_mode,
                            active_snapshot,
                            config,
                        ),
                        entry_interval_s=mode_entry_interval_s(
                            modeState.active_mode,
                            config,
                            active_snapshot,
                        ),
                    )
                scrollCompletedThisFrame = False
                if modeState.active_mode != previousMode:
                    timeAtStart = 0
                    prefetchedForModeSwitch = None
                    activeScrollCompletion = None
                    syncedEntryIndex = 0
                    active_snapshot = None
                    if modeState.active_mode in displayCaches:
                        active_cache = displayCaches[modeState.active_mode]
                        active_cache.refresh_if_stale(
                            now_monotonic,
                            active_cache.refresh_interval_s,
                        )
                        active_snapshot = active_cache.snapshot(now_monotonic).value
                elif (
                    modeState.active_mode in SCROLL_SYNCED_MODES
                    and activeScrollCompletion is not None
                    and activeScrollCompletion.complete
                ):
                    syncedEntryIndex += 1
                    activeScrollCompletion = None
                    scrollCompletedThisFrame = True

                refreshInterval = config["refreshTime"]
                if modeState.active_mode in {"adsb", "adsb-records", "plane-alert"}:
                    refreshInterval = mode_entry_interval_s(
                        modeState.active_mode,
                        config,
                        active_snapshot,
                    )

                if (
                    now_monotonic - lastCacheRefreshCheck
                    >= CACHE_REFRESH_CHECK_INTERVAL_S
                ):
                    lastCacheRefreshCheck = now_monotonic
                    if modeState.active_mode == "train":
                        displayCaches["train"].refresh_if_due(now_monotonic)

                    active_value = None
                    if modeState.active_mode in displayCaches:
                        active_value = displayCaches[modeState.active_mode].snapshot(
                            now_monotonic,
                        ).value
                    active_limit = active_mode_limit_s(
                        modeState.active_mode,
                        active_value,
                        config,
                    )
                    elapsed = now_monotonic - modeState.last_switch
                    if (
                        elapsed >= max(0.0, active_limit - PREFETCH_LEAD_TIME_S)
                        and prefetchedForModeSwitch != modeState.active_mode
                    ):
                        prefetch_modes_for_next_cycle(
                            displayCaches,
                            transportModes,
                            modeState.active_mode,
                            now_monotonic,
                        )
                        prefetchedForModeSwitch = modeState.active_mode

                shouldRedraw = timeNow - timeAtStart >= refreshInterval
                if modeState.active_mode in SCROLL_SYNCED_MODES:
                    if activeScrollCompletion is None:
                        shouldRedraw = (
                            scrollCompletedThisFrame
                            or timeNow - timeAtStart >= refreshInterval
                        )
                    else:
                        shouldRedraw = activeScrollCompletion.complete

                if shouldRedraw:
                    # check if debug mode is enabled
                    if config["debug"] == True:
                        print(config["debug"])
                        virtual = drawDebugScreen(device, width=widgetWidth, height=widgetHeight, showTime=True)
                        if config['dualScreen']:
                            virtual1 = drawDebugScreen(device1, width=widgetWidth, height=widgetHeight, showTime=True, screen="2")
                    elif modeState.active_mode == "adsb":
                        aircraft = displayCaches["adsb"].snapshot(now_monotonic).value
                        if aircraft is None:
                            virtual = drawBlankSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                departureStation="Loading ADS-B",
                            )
                            if config['dualScreen']:
                                virtual1 = drawBlankSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    departureStation="Loading ADS-B",
                                )
                        elif aircraft is not False:
                            activeScrollCompletion = ScrollCompletion(
                                SCROLL_REQUIRED_CYCLES,
                            )
                            virtual = drawAdsbSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                aircraft=aircraft,
                                mode_elapsed_s=(
                                    now_monotonic - modeState.last_switch
                                ),
                                entry_index=syncedEntryIndex,
                                scroll_completion=activeScrollCompletion,
                            )
                            if config['dualScreen']:
                                virtual1 = drawAdsbSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    aircraft=aircraft,
                                    mode_elapsed_s=(
                                        now_monotonic - modeState.last_switch
                                    ),
                                    entry_index=syncedEntryIndex,
                                )
                        elif config["transport"]["fallbackMode"] == "train":
                            data = displayCaches["train"].snapshot(now_monotonic).value
                            virtual, virtual1_candidate = draw_cached_train_signage(
                                device,
                                device1 if config['dualScreen'] else None,
                                widgetWidth,
                                widgetHeight,
                                data,
                            )
                            if config['dualScreen']:
                                virtual1 = virtual1_candidate
                        else:
                            virtual = drawBlankSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                departureStation="ADS-B unavailable",
                            )
                            if config['dualScreen']:
                                virtual1 = drawBlankSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    departureStation="ADS-B unavailable",
                                )
                    elif modeState.active_mode == "adsb-records":
                        boards = displayCaches["adsb-records"].snapshot(
                            now_monotonic,
                        ).value
                        if boards is None:
                            virtual = drawBlankSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                departureStation="Loading ADS-B records",
                            )
                            if config['dualScreen']:
                                virtual1 = drawBlankSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    departureStation="Loading ADS-B records",
                                )
                        elif boards is not False:
                            activeScrollCompletion = ScrollCompletion(
                                mode_scroll_required_cycles("adsb-records"),
                            )
                            virtual = drawAdsbRecordsSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                boards=boards,
                                mode_elapsed_s=(
                                    now_monotonic - modeState.last_switch
                                ),
                                entry_index=syncedEntryIndex,
                                scroll_completion=activeScrollCompletion,
                            )
                            if config['dualScreen']:
                                virtual1 = drawAdsbRecordsSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    boards=boards,
                                    mode_elapsed_s=(
                                        now_monotonic - modeState.last_switch
                                    ),
                                    entry_index=syncedEntryIndex,
                                )
                        elif config["transport"]["fallbackMode"] == "train":
                            data = displayCaches["train"].snapshot(now_monotonic).value
                            virtual, virtual1_candidate = draw_cached_train_signage(
                                device,
                                device1 if config['dualScreen'] else None,
                                widgetWidth,
                                widgetHeight,
                                data,
                            )
                            if config['dualScreen']:
                                virtual1 = virtual1_candidate
                        else:
                            virtual = drawBlankSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                departureStation="ADS-B records unavailable",
                            )
                            if config['dualScreen']:
                                virtual1 = drawBlankSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    departureStation="ADS-B records unavailable",
                                )
                    elif modeState.active_mode == "plane-alert":
                        alerts = displayCaches["plane-alert"].snapshot(now_monotonic).value
                        if alerts is None:
                            virtual = drawBlankSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                departureStation="Loading Plane-Alert",
                            )
                            if config['dualScreen']:
                                virtual1 = drawBlankSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    departureStation="Loading Plane-Alert",
                                )
                        elif alerts is not False:
                            activeScrollCompletion = ScrollCompletion(
                                SCROLL_REQUIRED_CYCLES,
                            )
                            virtual = drawPlaneAlertSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                alerts=alerts,
                                mode_elapsed_s=(
                                    now_monotonic - modeState.last_switch
                                ),
                                entry_index=syncedEntryIndex,
                                scroll_completion=activeScrollCompletion,
                            )
                            if config['dualScreen']:
                                virtual1 = drawPlaneAlertSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    alerts=alerts,
                                    mode_elapsed_s=(
                                        now_monotonic - modeState.last_switch
                                    ),
                                    entry_index=syncedEntryIndex,
                                )
                        elif config["transport"]["fallbackMode"] == "train":
                            data = displayCaches["train"].snapshot(now_monotonic).value
                            virtual, virtual1_candidate = draw_cached_train_signage(
                                device,
                                device1 if config['dualScreen'] else None,
                                widgetWidth,
                                widgetHeight,
                                data,
                            )
                            if config['dualScreen']:
                                virtual1 = virtual1_candidate
                        else:
                            virtual = drawBlankSignage(
                                device,
                                width=widgetWidth,
                                height=widgetHeight,
                                departureStation="Plane-Alert unavailable",
                            )
                            if config['dualScreen']:
                                virtual1 = drawBlankSignage(
                                    device1,
                                    width=widgetWidth,
                                    height=widgetHeight,
                                    departureStation="Plane-Alert unavailable",
                                )
                    else:
                        data = displayCaches["train"].snapshot(now_monotonic).value
                        virtual, virtual1_candidate = draw_cached_train_signage(
                            device,
                            device1 if config['dualScreen'] else None,
                            widgetWidth,
                            widgetHeight,
                            data,
                        )
                        if config['dualScreen']:
                            virtual1 = virtual1_candidate

                    timeAtStart = time.time()

                timeNow = time.time()
                virtual.refresh()
                if config['dualScreen']:
                    virtual1.refresh()

except KeyboardInterrupt:
    pass
except ValueError as err:
    print(f"Error: {err}")
finally:
    if "refreshExecutor" in locals():
        refreshExecutor.shutdown(wait=False, cancel_futures=True)
# except KeyError as err:
#     print(f"Error: Please ensure the {err} environment variable is set")
