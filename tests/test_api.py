"""Tests API. Les fixtures partagées sont définies dans conftest.py."""

from datetime import UTC, datetime


def test_predict_valid(client, valid_payload):
    r = client.post("/v1/predict", json=valid_payload)
    assert r.status_code == 200
    # 8 h à Lyon (+02:00) + 1 h = 7 h UTC, quel que soit le fuseau dans lequel l'API répond
    target = datetime.fromisoformat(r.json()["target_timestamp"])
    assert target == datetime(2026, 10, 6, 7, tzinfo=UTC)


def test_predict_rejects_bikes_above_capacity(client, valid_payload):
    r = client.post("/v1/predict", json={**valid_payload, "bikes_available": 25})
    assert r.status_code == 422


def test_predict_rejects_unknown_fields(client, valid_payload):
    r = client.post("/v1/predict", json={**valid_payload, "unexpected": "value"})
    assert r.status_code == 422


def test_predict_rejects_missing_fields(client, valid_payload):
    r = client.post(
        "/v1/predict",
        json={key: value for key, value in valid_payload.items() if key != "timestamp"},
    )
    assert r.status_code == 422


def test_predict_rejects_timestamp_without_timezone(client, valid_payload):
    r = client.post(
        "/v1/predict",
        json={**valid_payload, "timestamp": "2026-10-06T08:00:00"},
    )
    assert r.status_code == 422


def test_predict_rejects_out_of_range_values(client, valid_payload):
    invalid_payloads = [
        {**valid_payload, "station_id": 0},
        {**valid_payload, "capacity": 0},
        {**valid_payload, "capacity": 101},
        {**valid_payload, "temperature": -31},
        {**valid_payload, "temperature": 51},
    ]

    for payload in invalid_payloads:
        r = client.post("/v1/predict", json=payload)
        assert r.status_code == 422


def test_predict_result_is_within_capacity(client, valid_payload):
    r = client.post("/v1/predict", json=valid_payload)

    assert r.status_code == 200
    assert 0 <= r.json()["predicted_bikes"] <= valid_payload["capacity"]


def test_predict_supports_station_not_seen_during_training(client, valid_payload):
    payload = {**valid_payload, "station_id": 9999}

    r = client.post("/v1/predict", json=payload)

    assert r.status_code == 200
    assert r.json()["station_id"] == 9999


def test_health_is_live_and_ready_fails_without_model(client, monkeypatch):
    from velov.api import main

    monkeypatch.setitem(main.STATE, "model", None)
    monkeypatch.setitem(main.STATE, "metadata", None)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503


def test_predict_is_persisted_when_database_is_configured(
    client, valid_payload, monkeypatch
):
    from velov.api import main

    persisted = []
    monkeypatch.setenv("PGHOST", "db")
    monkeypatch.setattr(
        main, "save_prediction", lambda request, response: persisted.append((request, response))
    )

    r = client.post("/v1/predict", json=valid_payload)

    assert r.status_code == 200
    assert len(persisted) == 1
    assert persisted[0][0].station_id == valid_payload["station_id"]
    assert persisted[0][1].model_version == r.json()["model_version"]
