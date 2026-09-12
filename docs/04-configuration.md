# Configuration

Sign up for the [National Rail Enquiries OpenLDBWS API](http://realtime.nationalrail.co.uk/OpenLDBWSRegistration), which will generate a token for you to use as the API key.

Only the API key is required to make the project run, everything else is optional but of course it may make sense for you to at least choose your preferred your station.

These environment variables are specified using the [balenaCloud dashboard](https://www.balena.io/docs/learn/manage/serv-vars/), allowing you to set up multiple signs in one fleet for different stations.


| Key                              | Example Value
|----------------------------------|----------
|`apiKey` **(REQUIRED)** | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` (OpenLDBWS API key)
|`TZ`  | `Europe/London`, will default to UTC if not set ([timezones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones))
|`departureStation`  | `PAD` ([station code](https://www.nationalrail.co.uk/stations_destinations/48541.aspx))
|`destinationStation`  | `HWV` ([station code](https://www.nationalrail.co.uk/stations_destinations/48541.aspx)) [optional] Filters trains shown to only those that call at this station
|`timeOffset`  | `5` [optional] (Time offset, in minutes, for the departure board. Can be used to see into the future (positive value) or past (negative value). Set 5 if you live 5 min from the station and want to hide departures that are too soon to catch)
|`refreshTime` | `120` (seconds between data refresh)
|`screenRotation` | `2` (rotates the output of the OLED, 0 for when using the desk stand, 2 for the monitor mount ([docs](https://luma-oled.readthedocs.io/en/latest/api-documentation.html#luma.oled.device.ssd1322)))
|`operatingHours` | `8-22` (hours during which the data will refresh at the interval above - leave blank to run all day)
|`screenBlankHours` | `1-6` (hours during which the screen will be blank and data will not refresh - leave blank to never blank)
| `outOfHoursName` | `London Paddington` (name shown when current time is outside the `operatingHours`)
| `dualScreen` | `True` (if you are using two displays)
| `screen1Platform` | `1` (sets the platform you want to have displayed on the first or single-screen display)
| `screen2Platform` | `2` (sets the platform you want to have displayed on the second display)
| `individualStationDepartureTime` | `False` (Displays the estimated or scheduled time of the service at each leg of a journey)
| `fpsTime` | `4` (adjusts how often the effective FPS is displayed)
| `headless` | `True` (outputs to noop serial device rather than serial port; useful for running on a development machine)
| `showDepartureNumbers` | `True` (adds 1st / 2nd / 3rd as per UK train departures)
| `firstDepartureBold` | `False` (makes the first departure use either the bold or normal font)
| `loopDepartureCount` | `6` (number of upcoming departures, after the first, to loop through on the lower two rows)
| `loopDepartureInterval` | `10` (seconds between rotating the lower two rows to the next pair of departures)
| `lastLineText` | `****Last Line****` (end-of-list marker shown centered in ADS-B and Plane-Alert lower rows)
| `targetFPS` | `20` (Frame rate regulator FPS target; 0 disables the regulator, which will increase FPS on constrained CPU, but will run the CPU hot at 100%.)
| `debug` | `False` (Display debugging information; `True` shows the debug info permanently, any integer `>1` will show instead of the splash screen for that number of seconds)

## ADS-B aircraft mode (optional)

ADS-B support is disabled by default. When enabled, the display can alternate between the existing train board and a nearby-aircraft board using readsb/tar1090 `aircraft.json` output.

| Key | Example Value
|-----|----------
| `adsbEnabled` | `True` (enables ADS-B mode as an available transport mode; including `adsb`, `adsb-records`, `adsb-stats`, or `records` in `transportModes` also enables it)
| `transportModes` | `train,adsb,adsb-records` (ordered comma-separated modes to display; use `adsb` for live ADS-B only or `adsb-records` for records only; when unset or left as `train`, enabled optional boards are appended after `train`)
| `modeSwitchInterval` | `300` (seconds before switching to the next configured mode; ignored when `modeRunCount` is set)
| `modeRunCount` | `1` (optional; switch modes after each mode has cycled every entry this many times. Train mode doubles this count, so `1` runs train entries twice. ADS-B and Plane-Alert detail lines scroll twice before advancing to the next entry; ADS-B statistics detail lines scroll once before advancing.)
| `transportFallbackMode` | `train` (fallback shown if ADS-B fetch/parsing fails; set to anything else to show an ADS-B unavailable screen instead)
| `adsbSourceUrl` | `http://192.168.1.74/readsb/data/aircraft.json` (readsb/tar1090 JSON endpoint)
| `adsbHomeLat` | `51.501` (receiver/display latitude; required for ADS-B sorting)
| `adsbHomeLon` | `-0.142` (receiver/display longitude; required for ADS-B sorting)
| `adsbFetchTimeout` | `2` (HTTP timeout in seconds)
| `adsbUserAgent` | `Mozilla/5.0 TrainDepartureDisplay/ADS-B` (HTTP User-Agent sent to the ADS-B web proxy)
| `adsbRefreshTime` | `10` (minimum seconds between ADS-B JSON refresh attempts; cyclic mode refreshes are prefetched before ADS-B is displayed instead of repeatedly running during the animation)
| `adsbDisplayCount` | `5` (nearest aircraft to keep for the aircraft board)
| `adsbMaxAgeSeconds` | `30` (ignore aircraft not seen within this many seconds)
| `adsbMaxDistanceNm` | `100` (optional maximum distance in nautical miles; blank means no distance cap)
| `adsbMinAltitudeFt` | `1000` (optional minimum altitude in feet; blank means no altitude floor)
| `adsbRouteLookupEnabled` | `False` (enables a second-stage tar1090-compatible route lookup for origin/destination)
| `adsbRouteApiUrl` | `https://api.adsb.lol/api/0/routeset` (route lookup endpoint accepting a `planes` JSON POST body)
| `adsbRouteFetchTimeout` | `4` (HTTP timeout in seconds for the route lookup request)
| `adsbRouteDisplay` | `iata` (route label format: `iata`, `icao`, or `city`)
| `adsbTopLeftTemplate` | `{summary_left}` (highlight row left block)
| `adsbTopRightTemplate` | `{summary_right}` (highlight row right block)
| `adsbScrollTemplate` | `{detail}` (single scrolling row block; the default detail no longer includes `seen`)
| `adsbNextLeftTemplate` | `{loop_aircraft}` (next-aircraft detail left block)
| `adsbNextRightTemplate` | `{loop_info}` (next-aircraft detail right block)
| `adsbRecordsStorePath` | `/data/adsb-records.json` (persistent JSON store for `adsb-records` Last 24 Hours / Last 7 Days / All Time record boards; `/data` persists across balena device reboots and app updates)
| `adsbRecordsWindows` | `24hr,7 day,All Time` (comma-separated record boards to show; accepts `24hr`, `7 day`, and `All Time` plus aliases such as `day`, `week`, and `all-time`)

The `adsb-records` mode reuses the same filtered ADS-B aircraft as the live ADS-B board and stores observations locally after each successful ADS-B refresh. By default the store lives under `/data`, so collected statistics survive device reboots and balena application updates. It can rotate through Last 24 Hours, Last 7 Days, and All Time boards, controlled by `adsbRecordsWindows`. Each board tracks highest/lowest altitude, fastest/slowest speed, furthest/nearest distance, and strongest climb/descent where those fields exist in the ADS-B feed. Record boards paginate through every metric pair before moving to the next window, so metrics are not dropped when the mode cycles. The All Time board keeps best values even after old observations are pruned from the rolling week window. The mode uses the same OLED layout style as the aircraft board: a split top row, a scrolling summary row, two compact record rows, and a clock row labelled `Statistics`.

The ADS-B board skips aircraft without positions, because nearest-aircraft sorting requires latitude and longitude. When `adsbRouteLookupEnabled` is true, the app keeps the existing readsb/tar1090 aircraft JSON as the live aircraft source, then POSTs the displayed aircraft callsigns and positions to the configured `adsbRouteApiUrl`. Returned origin/destination data is best-effort and appears on the highlighted aircraft top line when available. The highlighted full-detail aircraft cycles through all displayed aircraft using two `loopDepartureInterval` periods; when the first aircraft is highlighted, the lower ADS-B rows show only the second and third aircraft, then the third and fourth when the second is highlighted, and so on. When the lower rows reach the end of the displayed aircraft list they show the configured `lastLineText` centered before the highlighted aircraft cycles back to the first entry. The ADS-B board also labels the clock row with `ADSB`. The default highlighted top line shows flight, route, registration, aircraft type, preferred speed, distance, and altitude. The highlighted scrolling detail line pauses briefly before moving, then shows description, bearing, track, ground speed, true airspeed, Mach, climb/descent rate, squawk, and hex when those fields are available. Secondary aircraft rows default to rank, flight, and type on the left, with speed, distance, and altitude grouped on the right. ADS-B JSON is refreshed in the background and cached before the next ADS-B display cycle where possible, then reused during that visible cycle so network reads, route lookup, and record-store writes do not compete with OLED scrolling. Network failures and malformed ADS-B JSON are handled separately from train loading so the train board can continue to run. Empty `201` route responses are treated as no-route results rather than route parse failures. The default `adsbUserAgent` avoids reverse proxy bot blocks that reject the default Python requests User-Agent.

### Trimming ADS-B statistics

The records store is JSON, not CSV. To remove bad rows safely, stop the app or run the command between display cycles, then use the trim helper with a dry run first:

```bash
python src/trim_adsb_records.py --store /data/adsb-records.json --hex BAD123 --dry-run
python src/trim_adsb_records.py --store /data/adsb-records.json --hex BAD123
```

Use `--label CALLSIGN` when the bad row is easiest to identify by display label/callsign. If the bad value has already become an All Time record, also clear the affected metric explicitly, for example `--forever-metric highest` or `--forever-metric furthest`. Multiple `--hex`, `--label`, and `--forever-metric` options can be supplied in one command.

ADS-B display templates use `{variable}` placeholders. Available variables are: `{summary_left}`, `{summary_right}`, `{summary}`, `{detail}`, `{loop_aircraft}`, `{loop_info}`, `{position}`, `{position_ordinal}`, `{display_name}`, `{flight}`, `{registration}`, `{hex}`, `{route}`, `{origin}`, `{destination}`, `{aircraft_type}`, `{description}`, `{latitude}`, `{longitude}`, `{distance_nm}`, `{distance}`, `{bearing_deg}`, `{bearing}`, `{altitude_ft}`, `{altitude}`, `{ground_speed_kt}`, `{speed}`, `{ground_speed}`, `{true_air_speed_kt}`, `{true_air_speed}`, `{summary_speed}`, `{mach_value}`, `{mach}`, `{track_deg}`, `{heading}`, `{vertical_rate_fpm}`, `{vertical_rate}`, `{squawk}`, `{squawk_label}`, `{seen_seconds}`, and `{seen}`.

Example custom ADS-B layout:

```env
adsbTopLeftTemplate={display_name} {route}
adsbTopRightTemplate={registration} {aircraft_type} {altitude}
adsbScrollTemplate={description}  {bearing}  {heading}  {ground_speed}  {squawk_label}  {hex}
adsbNextLeftTemplate={position_ordinal} {display_name} {aircraft_type}
adsbNextRightTemplate={speed} {distance} {altitude}
```

## Plane-Alert mode (optional)

Plane-Alert support is disabled by default. When enabled, the display can alternate to a docker-planefence Plane-Alert board using the same live history stream as the web UI: `/cgi/stream.sh?mode=plane-alert&date=all`. Legacy `pa_query.php` URLs are accepted for compatibility, but the app upgrades them to the live stream because `pa_query.php` can be stale. The stream is newline-delimited JSON (one JSON object per line); JSON arrays/objects and CSV exports remain supported as fallbacks.

| Key | Example Value
|-----|----------
| `planeAlertEnabled` | `True` (enables Plane-Alert mode as an available transport mode; including `plane-alert` in `transportModes` also enables it)
| `transportModes` | `train,adsb,plane-alert` (ordered comma-separated modes to display; `planealert` is also accepted; when unset or left as `train`, enabled ADS-B/Plane-Alert boards are appended after `train`)
| `modeSwitchInterval` | `300` (seconds before switching to the next configured mode; ignored when `modeRunCount` is set)
| `modeRunCount` | `1` (optional; switch modes after each mode has cycled every entry this many times. Train mode doubles this count, so `1` runs train entries twice. ADS-B and Plane-Alert detail lines scroll twice before advancing to the next entry; ADS-B statistics detail lines scroll once before advancing.)
| `transportFallbackMode` | `train` (fallback shown if Plane-Alert fetch/parsing fails; set to anything else to show a Plane-Alert unavailable screen instead)
| `planeAlertSourceUrl` | `http://192.168.1.74:8083/cgi/stream.sh?mode=plane-alert&date=all` (live Plane-Alert stream used by the web UI; legacy `pa_query.php` URLs are upgraded automatically)
| `planeAlertFetchTimeout` | `90` (HTTP timeout in seconds; the full live history stream can be slower than ADS-B JSON)
| `planeAlertUserAgent` | Browser-like Chrome User-Agent (HTTP User-Agent sent to the Plane-Alert web proxy; override only if your proxy requires something specific)
| `planeAlertRefreshTime` | `30` (seconds between Plane-Alert background JSON refresh attempts)
| `planeAlertDisplayCount` | `30` (maximum/latest Plane-Alert aircraft rows to keep; configured values above 30 are capped)
| `planeAlertMaxAgeHours` | `24` (optional maximum alert age in hours; blank means no age cap)
| `planeAlertTimeOffset` | `1` (hours to add to Plane-Alert timestamps before display/filtering; use `1` for BST when the source emits GMT)
| `planeAlertTopLeftTemplate` | `{summary_left}` (highlight row left block)
| `planeAlertTopRightTemplate` | `{summary_right}` (highlight row right block)
| `planeAlertScrollTemplate` | `{detail}` (single scrolling row block)
| `planeAlertNextLeftTemplate` | `{loop_alert}` (lower-row aircraft detail left block)
| `planeAlertNextRightTemplate` | `{db_category}  {loop_info}` (lower-row category and aircraft detail right block)

If a legacy or copied URL omits the `:8083` port, the app adds it when targeting the Plane-Alert live stream to avoid fetching `/cgi/stream.sh` from the train web server on port 80. The Plane-Alert board adds `planeAlertTimeOffset` hours to parsed timestamps, sorts alerts newest first using the live stream `index` when present, falling back to `timestamp` for legacy JSON/CSV data, then displays callsign, tail/hex, equipment, owner/name, distance, altitude, database category/tags, and route when present. The default highlighted scroll line omits hex, position/bearing-style data, and observed time. Like ADS-B mode, the highlighted full-detail Plane-Alert record cycles through all displayed aircraft using two `loopDepartureInterval` periods; the lower rows show the following records and then the configured `lastLineText` centered at the end of the list. The Plane-Alert board uses the same top-row layout as ADS-B and labels the clock row with `PLANE`. Plane-Alert data is refreshed in the background and cached before/while the mode is displayed, so slow history queries do not block OLED animation or mode transitions. The latest live-stream rows are selected by highest numeric `index`; the web UI row number is `index + 1`.

Plane-Alert display templates use `{variable}` placeholders. Available variables are: `{summary_left}`, `{summary_right}`, `{summary}`, `{detail}`, `{loop_alert}`, `{loop_info}`, `{loop_time}`, `{position}`, `{position_ordinal}`, `{display_name}`, `{call}`, `{tail}`, `{tail_or_hex}`, `{hex}`, `{index}`, `{raw_index}`, `{name}`, `{owner}`, `{equipment}`, `{aircraft_type}`, `{distance}`, `{altitude}`, `{db category}`, `{db_category}`, `{db tag1}`, `{db_tag1}`, `{db tag2}`, `{db_tag2}`, `{db tag3}`, `{db_tag3}`, `{route}`, `{timestamp}`, `{time}`, `{date_time}`, `{latitude}`, and `{longitude}`.

Example custom Plane-Alert mode layout:

```bash
planeAlertTopLeftTemplate={display_name} {tail_or_hex}
planeAlertTopRightTemplate={time}
planeAlertScrollTemplate={equipment}  {name}  {db_category}  {route}
planeAlertNextLeftTemplate={position_ordinal} {display_name} {tail}
planeAlertNextRightTemplate={db_category} {equipment}
```

If using two screens the following line needs to be added into /boot/config.txt which is achieved by using the 'Define DT overlays' option within the Device configuration screen on balenaCloud: `spi1-3cs`

![](images/overlays.png)
