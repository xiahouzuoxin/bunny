"""
Common utilities shared across model types.

Provides data loading, feature preparation, and metric computation
in a framework-agnostic way. Uses polars for data processing.
Copied into MODEL_DIR during scaffolding.
"""

import logging

import numpy as np
import polars as pl
from omegaconf import DictConfig, OmegaConf
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import LabelEncoder

log = logging.getLogger(__name__)


def get_task(cfg: DictConfig) -> str:
    """Get task type from config. Returns 'regression', 'binary', or 'multiclass'."""
    return cfg.get("task", "regression")


# ── Data Loading ─────────────────────────────────────────────────────────────

def load_data(path: str) -> pl.DataFrame:
    """Load data from CSV, Parquet, or S3 path."""
    if path.endswith(".csv"):
        return pl.read_csv(path)
    elif path.endswith(".parquet") or path.endswith(".pq"):
        return pl.read_parquet(path)
    elif path.startswith("s3://"):
        try:
            return pl.read_parquet(path)
        except Exception:
            return pl.read_csv(path)
    else:
        return pl.read_parquet(path)


# ── Feature Preparation ─────────────────────────────────────────────────────

def split_columns(
    df: pl.DataFrame, cfg: DictConfig
) -> tuple[list[str], list[str]]:
    """Identify numerical and categorical columns, excluding target and explicit excludes.

    Returns:
        num_cols, cat_cols
    """
    target_col = cfg.data.target_column
    exclude_cols = set(OmegaConf.to_container(cfg.data.get("exclude_columns", []), resolve=True) or [])
    exclude_cols.add(target_col)

    num_cols = []
    cat_cols = []
    for col in df.columns:
        if col in exclude_cols:
            continue
        if df[col].dtype == pl.Utf8 or df[col].dtype == pl.Categorical or df[col].dtype == pl.Boolean:
            cat_cols.append(col)
        else:
            num_cols.append(col)

    return num_cols, cat_cols


def prepare_features(
    df: pl.DataFrame, cfg: DictConfig
) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray, dict[str, LabelEncoder], list[str], list[str]]:
    """Prepare numerical and categorical features with encoding.

    Returns:
        numerical: (n_samples, n_num_features) standardized array
        categorical: {feat_name: (n_samples,) label-encoded array}
        labels: (n_samples,) target array
        label_encoders: {feat_name: fitted LabelEncoder}
        num_cols: list of numerical column names
        cat_cols: list of categorical column names
    """
    target_col = cfg.data.target_column
    task = get_task(cfg)
    if task in ("binary", "multiclass"):
        labels = df[target_col].to_numpy().astype(np.int64)
    else:
        labels = df[target_col].to_numpy().astype(np.float32)
    num_cols, cat_cols = split_columns(df, cfg)

    # Numerical: fill + standardize
    fill_val = cfg.data.get("numerical_fillna", -1.0)
    numerical = df.select([pl.col(c).fill_null(fill_val) for c in num_cols]).to_numpy().astype(np.float32)
    means = numerical.mean(axis=0)
    stds = numerical.std(axis=0) + 1e-8
    numerical = (numerical - means) / stds

    # Categorical: label-encode (shift by 1, 0 = padding)
    categorical = {}
    label_encoders = {}
    for col in cat_cols:
        le = LabelEncoder()
        filled = df[col].fill_null("__MISSING__").cast(pl.Utf8).to_numpy()
        encoded = le.fit_transform(filled) + 1
        categorical[col] = encoded.astype(np.int64)
        label_encoders[col] = le

    return numerical, categorical, labels, label_encoders, num_cols, cat_cols


def prepare_dataframe_features(
    df: pl.DataFrame, cfg: DictConfig
) -> tuple[pl.DataFrame, pl.Series, list[str], list[str]]:
    """Prepare features as a polars DataFrame (for tree models).

    Returns:
        X: feature DataFrame with filled missing values
        y: target Series
        cat_cols: categorical column names
        num_cols: numerical column names
    """
    target_col = cfg.data.target_column
    task = get_task(cfg)
    num_cols, cat_cols = split_columns(df, cfg)

    exclude_cols = set(OmegaConf.to_container(cfg.data.get("exclude_columns", []), resolve=True) or [])
    exclude_cols.add(target_col)
    drop_cols = [c for c in exclude_cols if c in df.columns]
    X = df.drop(drop_cols)

    # Fill missing values
    fill_val = cfg.data.get("numerical_fillna", -1.0)
    fill_exprs = []
    for col in cat_cols:
        if col in X.columns:
            fill_exprs.append(pl.col(col).fill_null("__MISSING__").cast(pl.Utf8))
    for col in num_cols:
        if col in X.columns:
            fill_exprs.append(pl.col(col).fill_null(fill_val))
    if fill_exprs:
        X = X.with_columns(fill_exprs)

    if task in ("binary", "multiclass"):
        y = df[target_col].cast(pl.Int64)
    else:
        y = df[target_col].cast(pl.Float32)

    return X, y, cat_cols, num_cols


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_regression_metrics(preds: np.ndarray, labels: np.ndarray) -> dict:
    """Compute standard regression metrics."""
    rmse = float(np.sqrt(np.mean((preds - labels) ** 2)))
    mae = float(np.mean(np.abs(preds - labels)))
    bias = float(np.mean(preds) - np.mean(labels))
    mean_label = float(np.mean(labels))
    bias_pct = (bias / mean_label * 100) if abs(mean_label) > 1e-8 else 0.0

    return {
        "rmse": rmse,
        "mae": mae,
        "bias": bias,
        "bias_pct": bias_pct,
        "mean_pred": float(np.mean(preds)),
        "mean_label": mean_label,
    }


def compute_classification_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_prob: np.ndarray | None = None, task: str = "binary"
) -> dict:
    """Compute classification metrics."""
    avg = "binary" if task == "binary" else "macro"
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, average=avg, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, average=avg, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, average=avg, zero_division=0)),
    }
    if y_prob is not None:
        try:
            if task == "binary":
                metrics["roc_auc"] = float(roc_auc_score(y_true, y_prob))
            else:
                metrics["roc_auc"] = float(roc_auc_score(y_true, y_prob, multi_class="ovr", average="macro"))
        except ValueError:
            pass
    return metrics


def compute_metrics(preds: np.ndarray, labels: np.ndarray, task: str = "regression", probs: np.ndarray | None = None) -> dict:
    """Compute metrics based on task type."""
    if task in ("binary", "multiclass"):
        return compute_classification_metrics(labels, preds, y_prob=probs, task=task)
    return compute_regression_metrics(preds, labels)


def log_metrics(metrics: dict, prefix: str = "") -> None:
    """Log metrics in the [METRIC] tagged format for autoresearch parsing."""
    parts = [f"{k}={v:.4f}" for k, v in metrics.items()]
    log.info(f"[METRIC] {prefix}{('  '.join(parts))}")
