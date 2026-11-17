"""Temps passé dans chaque étape de la prédiction, sans réseau ni serveur HTTP (TP5, étape 3).

Rejoue à la main les étapes de _predict() (src/velov/api/main.py), avec les MÊMES fonctions
(add_features, FEATURES) : sinon on chronomètre autre chose que l'API.

    python scripts/bench_predict.py --n 500 --batch 1 100 1000

Règles de mesure : time.perf_counter (pas time.time), un échauffement non compté, beaucoup
de répétitions, et des percentiles (p50, p95) plutôt qu'une moyenne. Le chargement du modèle
est fait une fois, avant, et n'est jamais compté. L'écriture en base de l'API n'est pas
rejouée ici : elle se lit par différence avec l'en-tête X-Process-Time-Ms.

Rédigé avec l'aide d'une IA (Claude, Anthropic).
"""

from __future__ import annotations

import argparse
import platform
import time
from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.features import FEATURES, add_features

PAYLOAD = {  # l'exemple du contrat, comme dans /docs
    "station_id": 1,
    "timestamp": "2026-10-06T08:00:00+02:00",
    "capacity": 20,
    "bikes_available": 12,
    "temperature": 14.5,
    "is_raining": False,
}
WARMUP = 20


def percentiles(samples: list[float]) -> str:
    ms = np.array(samples) * 1000
    return f"p50 {np.percentile(ms, 50):7.3f} ms   p95 {np.percentile(ms, 95):7.3f} ms"


def bench_steps(model, request: PredictionRequest, n: int) -> None:
    """Les quatre étapes de _predict(), chacune chronométrée séparément, n fois."""
    steps = ("1. DataFrame", "2. add_features", "3. model.predict", "4. réponse")
    times: dict[str, list[float]] = {name: [] for name in steps}
    for i in range(WARMUP + n):
        t0 = time.perf_counter()
        frame = pd.DataFrame([request.model_dump()])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        t1 = time.perf_counter()
        features = add_features(frame)[FEATURES]
        t2 = time.perf_counter()
        raw = float(model.predict(features)[0])
        prediction = min(max(raw, 0.0), float(request.capacity))
        t3 = time.perf_counter()
        PredictionResponse(
            station_id=request.station_id,
            target_timestamp=request.timestamp + timedelta(hours=1),
            predicted_bikes=prediction,
            model_version="bench",
        )
        t4 = time.perf_counter()
        if i >= WARMUP:  # l'échauffement n'est pas compté
            for name, start, end in zip(times, (t0, t1, t2, t3), (t1, t2, t3, t4), strict=True):
                times[name].append(end - start)

    total = [sum(parts) for parts in zip(*times.values(), strict=True)]
    print(f"Temps par étape, {n} prédictions d'une ligne (échauffement de {WARMUP} non compté)")
    for name, samples in times.items():
        share = 100 * np.median(samples) / np.median(total)
        print(f"  {name:<18} {percentiles(samples)}   ({share:4.1f} % du p50 total)")
    print(f"  {'total':<18} {percentiles(total)}")


def bench_batches(model, request: PredictionRequest, sizes: list[int], n: int) -> None:
    """Le temps de model.predict seul, selon le nombre de lignes prédites d'un coup."""
    frame = pd.DataFrame([request.model_dump()])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    one_row = add_features(frame)[FEATURES]
    print(f"\nmodel.predict selon la taille du lot ({n} répétitions)")
    for size in sizes:
        batch = pd.concat([one_row] * size, ignore_index=True)
        samples = []
        for i in range(WARMUP + n):
            start = time.perf_counter()
            model.predict(batch)
            if i >= WARMUP:
                samples.append(time.perf_counter() - start)
        per_row_us = np.median(samples) / size * 1e6
        print(f"  {size:>5} ligne(s)   {percentiles(samples)}   soit {per_row_us:9.1f} µs par ligne")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--n", type=int, default=500, help="nombre de mesures par cas (défaut : 500)")
    parser.add_argument("--batch", type=int, nargs="*", help="tailles de lot pour model.predict, ex. 1 100 1000")
    args = parser.parse_args()

    print(
        f"Machine : {platform.machine()}, Python {platform.python_version()}, "
        f"scikit-learn {sklearn.__version__}, pandas {pd.__version__}"
    )
    model = joblib.load(args.model_dir / "model.joblib")  # chargé une fois, jamais chronométré
    request = PredictionRequest(**PAYLOAD)  # validation Pydantic hors mesure, comme dans l'API
    bench_steps(model, request, args.n)
    if args.batch:
        bench_batches(model, request, args.batch, args.n)


if __name__ == "__main__":
    main()
