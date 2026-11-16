"""
CatBoost prediction / evaluation script.

Usage:
  python predict.py --config-name config
  python predict.py --config-name config predict.input_data=path/to/test.parquet
"""

import logging

import hydra
import numpy as np
from catboost import CatBoostClassifier, CatBoostRegressor, Pool
from omegaconf import DictConfig, OmegaConf

from common import compute_metrics, get_task, load_data, log_metrics, prepare_dataframe_features

log = logging.getLogger(__name__)


@hydra.main(config_path="conf", config_name="config", version_base=None)
def main(cfg: DictConfig):
    log.info(f"Starting prediction with configs:\n{OmegaConf.to_yaml(cfg)}")

    task = get_task(cfg)

    data_path = cfg.predict.get("input_data") or cfg.data.train_path
    log.info(f"Loading data from {data_path}")
    df = load_data(data_path)

    X, y, cat_cols, num_cols = prepare_dataframe_features(df, cfg)

    # Convert to pandas for CatBoost Pool
    X_pd = X.to_pandas()
    y_np = y.to_numpy()

    model_cfg = cfg.model
    if model_cfg.get("cat_features", "auto") == "auto":
        cat_feature_indices = [X_pd.columns.get_loc(c) for c in cat_cols]
    else:
        cat_names = list(OmegaConf.to_container(model_cfg.cat_features, resolve=True))
        cat_feature_indices = [X_pd.columns.get_loc(c) for c in cat_names if c in X_pd.columns]

    model_path = cfg.predict.get("model_path", "outputs/model.cbm")
    if task in ("binary", "multiclass"):
        model = CatBoostClassifier()
    else:
        model = CatBoostRegressor()
    model.load_model(model_path)
    log.info(f"Loaded model from {model_path}")

    test_pool = Pool(X_pd, y_np, cat_features=cat_feature_indices)

    if task in ("binary", "multiclass"):
        preds = model.predict(test_pool).astype(np.int64)
        probs = model.predict_proba(test_pool)
        if task == "binary":
            probs = probs[:, 1]
    else:
        preds = model.predict(test_pool)
        probs = None

    metrics = compute_metrics(preds, y_np, task=task, probs=probs)
    metrics["n_samples"] = len(y_np)
    log.info("Evaluation results:")
    log_metrics(metrics)


if __name__ == "__main__":
    main()
