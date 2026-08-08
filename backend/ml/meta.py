"""Мета-лейблинг на CatBoost: обучение на своих сделках + рил-тайм инференс.

Идея мета-лейблинга (Lopez de Prado): первичная модель — сам трейдер (его сделки),
а мета-модель предсказывает бинарную метку «сделка будет прибыльной» и решает,
пропускать ли сделку.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from ..config import (DEFAULT_THRESHOLD, MIN_TRADES_TO_TRAIN, MODEL_META_PATH,
                      MODEL_PATH)
from .features import FEATURE_COLUMNS

log = logging.getLogger("meta")


class MetaModel:
    def __init__(self):
        self.model = None
        self.trained = False
        self.metrics: dict = {}
        self.n_samples = 0

    # -------------------- persistence --------------------

    def load(self):
        """Блокирующая загрузка (импорт catboost тяжёлый) — вызывать в треде."""
        try:
            if os.path.exists(MODEL_PATH):
                from catboost import CatBoostClassifier
                m = CatBoostClassifier()
                m.load_model(MODEL_PATH)
                self.model = m
                self.trained = True
                if os.path.exists(MODEL_META_PATH):
                    with open(MODEL_META_PATH) as f:
                        meta = json.load(f)
                    self.metrics = meta.get("metrics", {})
                    self.n_samples = meta.get("n_samples", 0)
                log.info("meta model loaded: %s samples", self.n_samples)
        except Exception:
            log.exception("failed to load meta model")
            self.model = None
            self.trained = False

    def _save(self):
        if self.model is None:
            return
        self.model.save_model(MODEL_PATH)
        with open(MODEL_META_PATH, "w") as f:
            json.dump({"metrics": self.metrics, "n_samples": self.n_samples}, f)

    # -------------------- training --------------------

    def train(self, trades: List[dict]) -> dict:
        """trades — записи из БД с полями features (dict) и label (0/1)."""
        import numpy as np
        import pandas as pd
        from catboost import CatBoostClassifier, Pool
        from sklearn.metrics import accuracy_score, roc_auc_score

        rows, labels = [], []
        for t in trades:
            feats = t.get("features") or {}
            if not feats:
                continue
            rows.append({c: feats.get(c, 0.0) for c in FEATURE_COLUMNS})
            labels.append(int(t.get("label", 0)))

        if len(rows) < MIN_TRADES_TO_TRAIN:
            raise ValueError(
                f"Недостаточно сделок для обучения: {len(rows)} "
                f"(нужно минимум {MIN_TRADES_TO_TRAIN})"
            )

        X = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
        y = np.array(labels)

        # сплит по времени (сделки уже отсортированы по exit_ts DESC — разворачиваем)
        X = X.iloc[::-1].reset_index(drop=True)
        y = y[::-1]
        split = max(int(len(X) * 0.8), 1)
        X_tr, X_te = X.iloc[:split], X.iloc[split:]
        y_tr, y_te = y[:split], y[split:]

        model = CatBoostClassifier(
            iterations=300,
            depth=6,
            learning_rate=0.05,
            loss_function="Logloss",
            eval_metric="AUC",
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
        )
        model.fit(Pool(X_tr, y_tr))

        proba_te = model.predict_proba(X_te)[:, 1] if len(X_te) else model.predict_proba(X_tr)[:, 1]
        y_ref = y_te if len(X_te) else y_tr
        metrics = {
            "accuracy": float(accuracy_score(y_ref, proba_te > 0.5)),
            "pos_rate": float(y.mean()),
            "n_train": int(len(X_tr)),
            "n_test": int(len(X_te)),
        }
        try:
            metrics["auc"] = float(roc_auc_score(y_ref, proba_te))
        except Exception:
            metrics["auc"] = None

        self.model = model
        self.trained = True
        self.metrics = metrics
        self.n_samples = len(X)
        self._save()
        return metrics

    # -------------------- inference --------------------

    def predict_proba(self, features: dict) -> Optional[float]:
        if not self.trained or self.model is None or not features:
            return None
        import pandas as pd
        X = pd.DataFrame([{c: features.get(c, 0.0) for c in FEATURE_COLUMNS}],
                         columns=FEATURE_COLUMNS)
        return float(self.model.predict_proba(X)[0, 1])

    def status(self) -> dict:
        return {
            "trained": self.trained,
            "nSamples": self.n_samples,
            "metrics": self.metrics,
            "minTrades": MIN_TRADES_TO_TRAIN,
        }
