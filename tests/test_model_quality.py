"""Tests du modèle lui-même (TP4, Must 4) : ils tournent dans la CI à chaque push.

Un modèle peut se dégrader sans qu'aucun test de code n'échoue : l'API démarre, /ready
répond 200, mais les prédictions sont mauvaises (par exemple après un learning_rate passé
de 0.1 à 3.0). Ces tests entraînent un petit modèle sur des données simulées et vérifient :
- EX-05 : le modèle bat la baseline de persistance ;
- la reproductibilité : même seed et mêmes données donnent le même fichier, octet pour octet.

Jeu volontairement petit (30 jours, 5 stations, test sur 7 jours) : la CI doit rester rapide.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from velov.data import simulate
from velov.train import train

DAYS = 30
N_STATIONS = 5
TEST_DAYS = 7


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> Path:
    """Un historique simulé, identique à chaque exécution (seed fixe)."""
    path = tmp_path_factory.mktemp("quality") / "history.csv"
    simulate(days=DAYS, n_stations=N_STATIONS, seed=0).to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def trained(history, tmp_path_factory) -> tuple[Path, dict]:
    """Un modèle entraîné une seule fois pour tout le module (seed 42)."""
    out = tmp_path_factory.mktemp("model_a")
    metadata = train(history, out, version="0.0.0-ci", test_days=TEST_DAYS, seed=42)
    return out, metadata


def test_model_beats_persistence_baseline(trained):
    """EX-05 : sinon le modèle ne justifie pas sa complexité."""
    _, metadata = trained
    m = metadata["metrics"]
    assert m["mae_model"] < m["mae_baseline_persistence"], m


def test_peak_hours_metric_is_computed(trained):
    """La MAE aux heures de pointe existe et reste une erreur plausible."""
    _, metadata = trained
    m = metadata["metrics"]
    assert 0 <= m["mae_model_peak_hours"] < m["mae_baseline_persistence"] * 2, m


def test_same_seed_gives_same_artifact(trained, history, tmp_path_factory):
    """Reproductibilité : on réentraîne avec la même seed, le fichier doit être identique."""
    _, first = trained
    out = tmp_path_factory.mktemp("model_b")
    second = train(history, out, version="0.0.0-ci", test_days=TEST_DAYS, seed=42)
    assert second["artifact"]["sha256"] == first["artifact"]["sha256"]
    assert second["metrics"] == first["metrics"]
