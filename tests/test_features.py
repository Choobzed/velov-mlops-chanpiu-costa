import pandas as pd
import pytest

from velov.data import simulate
from velov.features import FEATURES, TARGET, add_features, make_training_frame


def test_add_features_does_not_mutate_input():
    df = simulate(days=2, n_stations=1)
    before = df.copy()
    add_features(df)
    pd.testing.assert_frame_equal(df, before)


def test_add_features_missing_column_raises():
    with pytest.raises(ValueError, match="Colonnes manquantes"):
        add_features(pd.DataFrame({"station_id": [1]}))


def test_calendar_features():
    df = simulate(days=2, n_stations=1, start="2026-10-03")  # samedi
    out = add_features(df)
    assert out.loc[0, "hour"] == 0
    assert out.loc[0, "day_of_week"] == 5
    assert out.loc[0, "is_weekend"] == 1
    assert set(FEATURES) <= set(out.columns)


def test_calendar_features_use_local_time():
    # 6 h UTC le 6 octobre = 8 h à Lyon (heure d'été) : le modèle voit l'heure locale.
    df = simulate(days=2, n_stations=1).head(1).assign(timestamp=pd.Timestamp("2026-10-06T06:00Z"))
    assert add_features(df).loc[0, "hour"] == 8


def test_add_features_rejects_naive_timestamps():
    df = simulate(days=2, n_stations=1)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="fuseau"):
        add_features(df)


def test_target_is_shifted_per_station_without_leakage():
    df = simulate(days=2, n_stations=2)
    frame = make_training_frame(df)
    # 48 h par station, la dernière heure n'a pas de cible : 47 lignes par station
    assert frame.groupby("station_id").size().tolist() == [47, 47]
    s1 = frame[frame["station_id"] == "1"].reset_index(drop=True)
    assert (s1[TARGET].iloc[:-1].to_numpy() == s1["bikes_available"].iloc[1:].to_numpy()).all()


def test_target_skips_missing_hours():
    # Vraies données : l'heure de 2 h manque pour la station. La ligne de 1 h ne doit pas
    # recevoir comme cible le nombre de vélos de 3 h (deux heures plus tard).
    df = simulate(days=2, n_stations=1).head(4)  # 0 h, 1 h, 2 h, 3 h
    df = df.drop(index=2).reset_index(drop=True)  # 0 h, 1 h, 3 h
    frame = make_training_frame(df)
    assert frame["timestamp"].tolist() == [df.loc[0, "timestamp"]]  # seule la paire 0 h -> 1 h est valable
    assert frame.loc[0, TARGET] == df.loc[1, "bikes_available"]
