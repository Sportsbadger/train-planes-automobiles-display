from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BALENA_YML = PROJECT_ROOT / "balena.yml"


def test_balena_defaults_use_live_plane_alert_stream():
    text = BALENA_YML.read_text(encoding="utf-8")

    assert "planeAlertSourceUrl: http://192.168.1.74:8083/cgi/stream.sh?mode=plane-alert&date=all" in text
    assert "planeAlertDisplayCount: 30" in text
    assert "pa_query.php" not in text


def test_balena_default_rotation_includes_plane_alert_when_enabled():
    text = BALENA_YML.read_text(encoding="utf-8")

    assert "planeAlertEnabled: True" in text
    assert "transportModes: train,plane-alert" in text
    assert "intermissionDuration: 2" in text


def test_balena_adsb_records_default_uses_persistent_data_path():
    text = BALENA_YML.read_text(encoding="utf-8")

    assert "adsbRecordsStorePath: /data/adsb-records.json" in text
    assert "adsbRecordsWindows: 24hr,7 day,All Time" in text
