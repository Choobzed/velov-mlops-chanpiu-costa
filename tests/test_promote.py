"""Tests de la règle de promotion et du registre (TP4, Must 3).

Deux niveaux :
- decide() seule, sur des scores écrits à la main : la règle, cas par cas ;
- le scénario complet sur un vrai registre MLflow, dans un fichier SQLite temporaire :
  entraîner, inscrire, promouvoir, refuser, et vérifier les alias et les tags.
"""

from __future__ import annotations

import pytest

from velov.promote import decide

WINDOW = {"test_start": "2026-10-04T21:00:00+00:00", "test_end": "2026-10-18T20:00:00+00:00"}


def scores(mae_model: float, mae_baseline: float = 2.0, **window) -> dict:
    return {"mae_model": mae_model, "mae_baseline_persistence": mae_baseline, **WINDOW, **window}


def test_first_champion_must_beat_persistence():
    assert decide(scores(1.5), None) == (True, "premier champion")
    ok, reason = decide(scores(2.1), None)
    assert not ok and "persistance" in reason


def test_challenger_must_beat_persistence_even_if_better_than_champion():
    ok, reason = decide(scores(2.5, mae_baseline=2.4), scores(3.0, mae_baseline=2.4))
    assert not ok and "persistance" in reason


def test_gain_of_two_percent_is_required():
    champion = scores(1.500)
    assert decide(scores(1.470), champion)[0]  # 1.470 <= 1.500 x 0.98 = 1.470 : juste assez
    ok, reason = decide(scores(1.490), champion)  # -0,7 % : de l'ordre du bruit entre seeds
    assert not ok and "insuffisant" in reason


def test_different_windows_are_not_compared():
    ok, reason = decide(scores(1.0), scores(1.5, test_start="2026-09-01T00:00:00+00:00"))
    assert not ok and "fenêtres" in reason


# --- Le scénario complet, sur un registre MLflow temporaire -------------------------------


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Un serveur MLflow n'est pas nécessaire : un fichier SQLite suffit pour le registre."""
    pytest.importorskip("mlflow")
    from velov.data import simulate

    monkeypatch.chdir(tmp_path)  # les artefacts MLflow (./mlruns) restent dans le dossier temporaire
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    data = tmp_path / "history.csv"
    simulate(days=30, n_stations=5, seed=0).to_csv(data, index=False)
    return data


def _train_and_register(data, tmp_path, monkeypatch, *, max_iter: int, seed: int = 42) -> str:
    import velov.train
    from velov.tracking import log_run

    monkeypatch.setattr(velov.train, "MAX_ITER", max_iter)
    out = tmp_path / f"models-{max_iter}-{seed}"
    metadata = velov.train.train(data, out, version="0.0.0-test", test_days=7, seed=seed)
    _, version = log_run(metadata, out, register=True)
    return version


def test_promotion_scenario(registry, tmp_path, monkeypatch):
    import mlflow

    from velov.promote import promote
    from velov.tracking import REGISTERED_MODEL_NAME as NAME

    client = mlflow.MlflowClient()

    # 1. Un modèle faible mais meilleur que la persistance : premier champion.
    v1 = _train_and_register(registry, tmp_path, monkeypatch, max_iter=20)
    assert str(client.get_model_version_by_alias(NAME, "challenger").version) == v1
    dry = promote(registry, test_days=7)
    assert dry["promote"] and not dry["applied"]
    with pytest.raises(mlflow.exceptions.MlflowException):  # sans --apply, rien n'a changé
        client.get_model_version_by_alias(NAME, "champion")
    promote(registry, test_days=7, apply=True)
    assert str(client.get_model_version_by_alias(NAME, "champion").version) == v1

    # 2. Un modèle nettement meilleur : il devient champion, l'ancien devient previous-champion.
    v2 = _train_and_register(registry, tmp_path, monkeypatch, max_iter=200)
    r = promote(registry, test_days=7, apply=True)
    assert r["promote"], r["reason"]
    assert str(client.get_model_version_by_alias(NAME, "champion").version) == v2
    assert str(client.get_model_version_by_alias(NAME, "previous-champion").version) == v1
    assert client.get_model_version(NAME, v2).tags["promotion"].startswith("promue")

    # 3. Même modèle, autre seed : un gain de l'ordre du bruit, refusé.
    v3 = _train_and_register(registry, tmp_path, monkeypatch, max_iter=200, seed=7)
    r = promote(registry, test_days=7, apply=True)
    assert not r["promote"], r["reason"]
    assert r["challenger"]["test_start"] == r["champion"]["test_start"]  # même fenêtre
    assert str(client.get_model_version_by_alias(NAME, "champion").version) == v2
    assert client.get_model_version(NAME, v3).tags["promotion"].startswith("refusée")
