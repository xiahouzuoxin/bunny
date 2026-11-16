"""
CatBoost training script for autoresearch.

Usage:
  python train.py --config-name config
  python train.py --config-name config model.iterations=2000 model.depth=8
"""

import logging
import os
import time

import hydra
import numpy as np
from catboost import CatBoostClassifier, CatBoostRegressor, Pool
from omegaconf import DictConfig, OmegaConf
from sklearn.model_selection import train_test_split

from common import compute_metrics, get_task, load_data, log_metrics, prepare_dataframe_features

log = logging.getLogger(__name__)


def get_defaults(task: str) -> tuple[str, str]:
    """Return default (loss_function, eval_metric) for the task."""
    if task == "binary":
        return "Logloss", "AUC"
    elif task == "multiclass":
        return "MultiClass", "Accuracy"
    return "RMSE", "RMSE"


@hydra.main(config_path="conf", config_name="config", version_base=None)
def main(cfg: DictConfig):
    log.info(f"Starting training with configs:\n{OmegaConf.to_yaml(cfg)}")

    task = get_task(cfg)
    log.info(f"Task type: {task}")

    seed = cfg.train.get("random_seed", 42)

    # Load data
    log.info(f"Loading data from {cfg.data.train_path}")
    df = load_data(cfg.data.train_path)
    log.info(f"Loaded {df.shape[0]} rows, {df.shape[1]} columns")

    X, y, cat_cols, num_cols = prepare_dataframe_features(df, cfg)
    log.info(f"Features: {len(num_cols)} numerical, {len(cat_cols)} categorical")

    # Convert to pandas for CatBoost Pool
    X_pd = X.to_pandas()
    y_np = y.to_numpy()

    # Resolve cat_features
    model_cfg = cfg.model
    if model_cfg.get("cat_features", "auto") == "auto":
        cat_feature_indices = [X_pd.columns.get_loc(c) for c in cat_cols]
    else:
        cat_names = list(OmegaConf.to_container(model_cfg.cat_features, resolve=True))
        cat_feature_indices = [X_pd.columns.get_loc(c) for c in cat_names if c in X_pd.columns]

    feat_names = X_pd.columns.tolist()

    # Train/test split
    test_frac = cfg.train.get("test_frac", 0.2)
    stratify = y_np if task in ("binary", "multiclass") else None
    X_train, X_test, y_train, y_test = train_test_split(X_pd, y_np, test_size=test_frac, random_state=seed, stratify=stratify)

    train_pool = Pool(X_train, y_train, cat_features=cat_feature_indices)
    test_pool = Pool(X_test, y_test, cat_features=cat_feature_indices)

    # Select loss/metric defaults based on task
    default_loss, default_metric = get_defaults(task)

    # Build model
    model_cls = CatBoostClassifier if task in ("binary", "multiclass") else CatBoostRegressor
    # Only pass params explicitly set in config; CatBoost uses its own defaults for the rest
    cb_params = dict(
        iterations=model_cfg.get("iterations", 1000),
        loss_function=model_cfg.get("loss_function", default_loss),
        eval_metric=model_cfg.get("eval_metric", default_metric),
        random_seed=seed,
        task_type=model_cfg.get("task_type", "CPU"),
        verbose=model_cfg.get("verbose", 100),
        early_stopping_rounds=model_cfg.get("early_stopping_rounds", None) or None,
    )
    for param in ("depth", "learning_rate", "l2_leaf_reg", "min_data_in_leaf",
                   "subsample", "colsample_bylevel"):
        if model_cfg.get(param) is not None:
            cb_params[param] = model_cfg[param]

    model = model_cls(**cb_params)

    # Timeout callback
    timeout_seconds = cfg.train.get("timeout_seconds", None)
    t_start = time.time()

    class TimeoutCallback:
        def after_iteration(self, info):
            elapsed = time.time() - t_start
            if timeout_seconds and elapsed >= timeout_seconds:
                log.info(f"Timeout reached ({elapsed:.0f}s >= {timeout_seconds}s) at iteration {info.iteration}. Stopping.")
                return False
            return True

    callbacks = [TimeoutCallback()] if timeout_seconds else None

    log.info(f"Training CatBoost model ({model_cls.__name__})...")
    model.fit(train_pool, eval_set=test_pool, callbacks=callbacks)

    # Evaluate on both train and test for overfitting analysis
    if task in ("binary", "multiclass"):
        preds = model.predict(test_pool).astype(np.int64)
        probs = model.predict_proba(test_pool)
        if task == "binary":
            probs = probs[:, 1]
        train_preds = model.predict(train_pool).astype(np.int64)
        train_probs = model.predict_proba(train_pool)
        if task == "binary":
            train_probs = train_probs[:, 1]
    else:
        preds = model.predict(test_pool)
        probs = None
        train_preds = model.predict(train_pool)
        train_probs = None

    test_metrics = compute_metrics(preds, y_test, task=task, probs=probs)
    train_metrics = compute_metrics(train_preds, y_train, task=task, probs=train_probs)
    test_metrics["best_iteration"] = model.get_best_iteration()

    log.info("=" * 60)
    log.info("Final test metrics:")
    log_metrics(test_metrics)

    # Overfitting analysis: train vs test gap
    log.info("=" * 60)
    log.info("[DIAGNOSTIC] Train vs Test gap (overfitting check):")
    for key in test_metrics:
        if key in train_metrics:
            gap = train_metrics[key] - test_metrics[key]
            log.info(f"  {key}: train={train_metrics[key]:.4f}  test={test_metrics[key]:.4f}  gap={gap:+.4f}")

    # Per-class metrics for classification
    if task == "binary":
        from sklearn.metrics import confusion_matrix as cm_func
        cm = cm_func(y_test, preds)
        tn, fp, fn, tp = cm.ravel()
        log.info("[DIAGNOSTIC] Confusion matrix:")
        log.info(f"  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
        log.info(f"  False positive rate: {fp/(fp+tn):.4f}  False negative rate: {fn/(fn+tp):.4f}")

    # Feature importance (all features, sorted)
    importance = model.get_feature_importance()
    top_k = sorted(zip(feat_names, importance), key=lambda x: -x[1])
    log.info("[DIAGNOSTIC] Feature importance (all features):")
    for name, imp in top_k:
        log.info(f"  {name}: {imp:.2f}")

    # SHAP-based feature interactions (if shap available)
    try:
        shap_values = model.get_feature_importance(train_pool, type="ShapValues")
        # shap_values has shape (n_samples, n_features + 1), last col is base value
        shap_abs_mean = np.abs(shap_values[:, :-1]).mean(axis=0)
        shap_sorted = sorted(zip(feat_names, shap_abs_mean), key=lambda x: -x[1])
        log.info("[DIAGNOSTIC] SHAP importance (mean |SHAP|):")
        for name, shap_val in shap_sorted:
            log.info(f"  {name}: {shap_val:.4f}")
    except Exception:
        pass  # SHAP not available or failed

    # Save model
    output_dir = cfg.train.get("output_dir", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    model_path = os.path.join(output_dir, "model.cbm")
    model.save_model(model_path)
    log.info(f"Model saved to {model_path}")


if __name__ == "__main__":
    main()
