"""Tests de velov.monitor (TP6, étape 4), sans base de données : sur des tables écrites à la main."""

from __future__ import annotations

import pandas as pd
import pytest

from velov.monitor import alerts, performance, with_occupancy, with_truth


def week(station_id: int, bikes: list[int], predicted: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2026-11-09T00:00Z", periods=len(bikes), freq="h")
    return pd.DataFrame(
        {
            "station_id": station_id,
            "timestamp": ts,
            "capacity": 20,
            "bikes_available": bikes,
            "temperature": 10.0,
            "is_raining": False,
            "predicted_bikes": predicted,
        }
    )


def test_truth_is_the_next_hour_of_the_same_station():
    df = pd.concat([week(1, [5, 7, 9], [6.0, 8.0, 0.0]), week(2, [1, 1, 4], [1.0, 3.0, 0.0])])
    scored = with_truth(df).sort_values(["station_id", "timestamp"])
    # La dernière heure de chaque station n'a pas encore de vérité : 2 lignes par station.
    assert scored.groupby("station_id").size().to_dict() == {1: 2, 2: 2}
    assert scored["truth"].tolist() == [7, 9, 1, 4]
    assert scored["err_model"].tolist() == [1.0, 1.0, 0.0, 1.0]  # |6-7|, |8-9|, |1-1|, |3-4|
    assert scored["err_persistence"].tolist() == [2, 2, 0, 3]  # |5-7|, |7-9|, |1-1|, |1-4|


def test_performance_and_ratio():
    perf = performance(week(1, [5, 7, 9], [6.0, 8.0, 0.0]))
    assert perf["n"] == 2
    assert perf["mae_model"] == pytest.approx(1.0)
    assert perf["mae_persistence"] == pytest.approx(2.0)
    assert perf["ratio"] == pytest.approx(0.5)


def test_alert_when_a_station_does_worse_than_persistence():
    n = 30  # au moins un jour de vérité par station
    good = week(1, [i % 10 for i in range(n)], [float((i + 1) % 10) for i in range(n)])  # parfait
    bad = week(2, [10] * n, [0.0] * n)  # persistance parfaite, modèle à côté
    found = alerts(performance(pd.concat([good, bad])))
    assert any("station 2" in a for a in found)
    assert not any("station 1" in a for a in found)


def test_no_verdict_on_a_station_with_too_few_hours():
    bad = week(2, [10, 10, 10], [0.0, 0.0, 0.0])
    assert not any("station" in a for a in alerts(performance(bad), max_ratio=99))


def test_drift_detects_a_cold_week():
    pytest.importorskip("evidently")
    from velov.monitor import drift

    base = pd.concat([week(s, [i % 15 for i in range(200)], [float(i % 15) for i in range(200)]) for s in (1, 2)])
    reference = with_occupancy(base.assign(temperature=[15.0 + (i % 7) for i in range(len(base))]))
    current = reference.assign(temperature=reference["temperature"] - 15)  # le scénario « froid »
    results = {d["column"]: d for d in drift(reference, current)}
    assert results["temperature"]["drift"]
    assert not results["bikes_available"]["drift"]
