"""Раздельные CatBoost meta-модели для входа/усреднения и выхода."""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from ..config import (ENTRY_MODEL_META_PATH, ENTRY_MODEL_PATH, EXIT_MODEL_META_PATH,
                      EXIT_MODEL_PATH, FEATURE_CORRELATION_THRESHOLD,
                      MIN_TRADES_TO_TRAIN)
from .features import FEATURE_COLUMNS

log = logging.getLogger("meta")


class MetaModel:
    def __init__(self, kind: str = "entry"):
        if kind not in ("entry", "exit"):
            raise ValueError("kind должен быть entry или exit")
        self.kind = kind
        self.model_path = ENTRY_MODEL_PATH if kind == "entry" else EXIT_MODEL_PATH
        self.meta_path = ENTRY_MODEL_META_PATH if kind == "entry" else EXIT_MODEL_META_PATH
        self.model = None
        self.trained = False
        self.metrics: dict = {}
        self.n_samples = 0
        self.selected_features = list(FEATURE_COLUMNS)
        self.dropped_features: dict[str, str] = {}

    def load(self):
        try:
            if not os.path.exists(self.model_path):
                return
            from catboost import CatBoostClassifier
            model = CatBoostClassifier()
            model.load_model(self.model_path)
            self.model = model
            self.trained = True
            if os.path.exists(self.meta_path):
                with open(self.meta_path) as file:
                    meta = json.load(file)
                self.metrics = meta.get("metrics", {})
                self.n_samples = meta.get("n_samples", 0)
                self.selected_features = meta.get("selected_features") or list(FEATURE_COLUMNS)
                self.dropped_features = meta.get("dropped_features") or {}
            log.info("%s meta model loaded: %s samples, %s features", self.kind,
                     self.n_samples, len(self.selected_features))
        except Exception:
            log.exception("failed to load %s meta model", self.kind)
            self.model = None
            self.trained = False

    def _save(self):
        if self.model is None:
            return
        self.model.save_model(self.model_path)
        with open(self.meta_path, "w") as file:
            json.dump({
                "kind": self.kind, "metrics": self.metrics, "n_samples": self.n_samples,
                "selected_features": self.selected_features,
                "dropped_features": self.dropped_features,
                "correlation_threshold": FEATURE_CORRELATION_THRESHOLD,
            }, file, indent=2)

    @staticmethod
    def select_uncorrelated_features(frame, threshold: float = FEATURE_CORRELATION_THRESHOLD):
        """Удаляет константы и более поздние признаки с |corr| >= threshold."""
        selected = []
        dropped: dict[str, str] = {}
        for column in frame.columns:
            series = frame[column]
            if series.nunique(dropna=False) <= 1:
                dropped[column] = "constant"
                continue
            correlated_with = None
            for kept in selected:
                corr = series.corr(frame[kept])
                if corr == corr and abs(corr) >= threshold:  # corr == corr исключает NaN
                    correlated_with = kept
                    break
            if correlated_with:
                dropped[column] = correlated_with
            else:
                selected.append(column)
        if not selected:
            raise ValueError("После очистки не осталось информативных фич")
        return selected, dropped

    def train(self, samples: List[dict]) -> dict:
        import numpy as np
        import pandas as pd
        from catboost import CatBoostClassifier, Pool
        from sklearn.metrics import accuracy_score, roc_auc_score

        rows, labels = [], []
        for sample in samples:
            features = sample.get("features") or {}
            label = sample.get("label")
            if not features or label is None:
                continue
            rows.append({column: features.get(column, 0.0) for column in FEATURE_COLUMNS})
            labels.append(int(label))
        if len(rows) < MIN_TRADES_TO_TRAIN:
            raise ValueError(f"Недостаточно {self.kind}-событий: {len(rows)} "
                             f"(нужно минимум {MIN_TRADES_TO_TRAIN})")
        if len(set(labels)) < 2:
            raise ValueError(f"Для {self.kind}-модели нужны примеры обоих классов")

        # samples приходят newest-first; обучение и split должны идти по времени.
        X = pd.DataFrame(rows, columns=FEATURE_COLUMNS).iloc[::-1].reset_index(drop=True)
        y = np.asarray(labels[::-1])
        split = min(max(int(len(X) * .8), 1), len(X) - 1)
        X_train_all, X_test_all = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y[:split], y[split:]
        if len(set(y_train)) < 2:
            # На маленькой хронологической выборке последний класс мог оказаться
            # только в test; в таком случае обучаемся на всей выборке.
            X_train_all, y_train = X, y
            X_test_all, y_test = X.iloc[0:0], y[0:0]

        selected, dropped = self.select_uncorrelated_features(X_train_all)
        X_train = X_train_all[selected]
        X_test = X_test_all[selected]
        model = CatBoostClassifier(
            iterations=400, depth=6, learning_rate=.04, loss_function="Logloss",
            eval_metric="AUC", random_seed=42, verbose=False, allow_writing_files=False,
            auto_class_weights="Balanced",
        )
        model.fit(Pool(X_train, y_train))
        eval_X, eval_y = (X_test, y_test) if len(X_test) else (X_train, y_train)
        probabilities = model.predict_proba(eval_X)[:, 1]
        metrics = {
            "accuracy": float(accuracy_score(eval_y, probabilities > .5)),
            "pos_rate": float(y.mean()), "n_train": int(len(X_train)),
            "n_test": int(len(X_test)), "features_total": len(FEATURE_COLUMNS),
            "features_selected": len(selected), "features_dropped": len(dropped),
        }
        try:
            metrics["auc"] = float(roc_auc_score(eval_y, probabilities))
        except Exception:
            metrics["auc"] = None

        self.model = model
        self.trained = True
        self.metrics = metrics
        self.n_samples = len(X)
        self.selected_features = selected
        self.dropped_features = dropped
        self._save()
        return metrics

    def predict_proba(self, features: dict) -> Optional[float]:
        if not self.trained or self.model is None or not features:
            return None
        import pandas as pd
        columns = self.selected_features
        frame = pd.DataFrame([{column: features.get(column, 0.0) for column in columns}],
                             columns=columns)
        return float(self.model.predict_proba(frame)[0, 1])

    def status(self) -> dict:
        return {
            "kind": self.kind, "trained": self.trained, "nSamples": self.n_samples,
            "metrics": self.metrics, "minTrades": MIN_TRADES_TO_TRAIN,
            "selectedFeatures": self.selected_features,
            "droppedFeatures": self.dropped_features,
        }
