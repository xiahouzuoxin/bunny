"""
analyze_data.py — Pre-training data analysis for autoresearch.

Prints structured analysis directly to stdout for the agent to read
and use when deciding feature engineering strategies. Uses polars for
fast data processing.

This script is scaffolded into MODEL_DIR and can be customized
per-model. It reads data config from conf/config.yaml via OmegaConf
(no Hydra decorator — stdout is clean and can be piped/redirected).

Usage:
  python analyze_data.py
  python analyze_data.py --config-name config
  python analyze_data.py --config conf/config.yaml --top-k 30 --max-rows 100000
"""

import argparse
import sys

import numpy as np
import polars as pl
from omegaconf import OmegaConf
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import LabelEncoder


def load_config(path: str):
    cfg = OmegaConf.load(path)
    return cfg


def load_data(path: str) -> pl.DataFrame:
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


def get_numeric_cols(df: pl.DataFrame, target_col: str, exclude: set) -> list[str]:
    return [
        col for col in df.columns
        if col != target_col
        and col not in exclude
        and df[col].dtype.is_numeric()
    ]


def get_categorical_cols(df: pl.DataFrame, target_col: str, exclude: set) -> list[str]:
    return [
        col for col in df.columns
        if col != target_col
        and col not in exclude
        and (df[col].dtype == pl.Utf8 or df[col].dtype == pl.Categorical or df[col].dtype == pl.Boolean)
    ]


def feature_stats(df: pl.DataFrame, target_col: str, exclude: set):
    """Compute per-feature statistics."""
    rows = []
    for col in df.columns:
        if col == target_col or col in exclude:
            continue
        s = df[col]
        is_cat = s.dtype == pl.Utf8 or s.dtype == pl.Categorical or s.dtype == pl.Boolean
        dtype = "cat" if is_cat else "num"
        missing_pct = round(s.null_count() / len(df) * 100, 1)
        n_unique = s.n_unique()
        row = f"  {col:30s}  dtype={dtype:3s}  missing={missing_pct:5.1f}%  nunique={n_unique}"
        if not is_cat:
            mean = s.drop_nulls().mean()
            std = s.drop_nulls().std()
            skew_val = s.drop_nulls().skew()
            if mean is not None:
                row += f"  mean={mean:.4f}  std={std:.4f}  skew={skew_val:.2f}"
        rows.append((row, dtype, missing_pct, n_unique, col))
    lines = [r[0] for r in rows]
    return "\n".join(lines), rows


def numeric_correlations(df: pl.DataFrame, target_col: str, exclude: set) -> list[tuple[str, float]]:
    """Pearson correlation of each numeric feature with target."""
    if not df[target_col].dtype.is_numeric():
        return []
    results = []
    num_cols = get_numeric_cols(df, target_col, exclude)
    for col in num_cols:
        valid = df.select([col, target_col]).drop_nulls()
        if len(valid) < 10:
            continue
        corr = valid.select(pl.corr(col, target_col)).item()
        if corr is not None and not np.isnan(corr):
            results.append((col, round(corr, 4)))
    return sorted(results, key=lambda x: -abs(x[1]))


def categorical_target_rates(df: pl.DataFrame, target_col: str, exclude: set, task: str) -> list[dict]:
    """For each categorical feature, show target rate by group."""
    results = []
    cat_cols = get_categorical_cols(df, target_col, exclude)
    for col in cat_cols:
        grouped = df.group_by(col).agg(pl.col(target_col).mean().alias("rate"))
        n_groups = len(grouped)
        rates = grouped.sort("rate", descending=True)

        if task == "binary":
            rate_min = rates["rate"].min()
            rate_max = rates["rate"].max()
            top_groups = {
                str(row[col]): round(row["rate"], 3)
                for row in rates.head(5).iter_rows(named=True)
            }
            results.append({
                "feature": col,
                "n_groups": n_groups,
                "target_rate_range": f"{rate_min:.3f} - {rate_max:.3f}",
                "top_groups": top_groups,
            })
        else:
            rate_min = rates["rate"].min()
            rate_max = rates["rate"].max()
            results.append({
                "feature": col,
                "n_groups": n_groups,
                "target_mean_range": f"{rate_min:.3f} - {rate_max:.3f}",
            })
    return results


def single_feature_auc(df: pl.DataFrame, target_col: str, exclude: set) -> list[tuple[str, float]]:
    """Compute AUC of each feature predicting the binary target individually."""
    if df[target_col].n_unique() != 2:
        return []
    results = []
    for col in df.columns:
        if col == target_col or col in exclude:
            continue
        s = df[col]
        is_cat = s.dtype == pl.Utf8 or s.dtype == pl.Categorical or s.dtype == pl.Boolean
        valid = df.select([col, target_col]).drop_nulls()
        if len(valid) < 10:
            continue

        try:
            y_true = valid[target_col].to_numpy()
            if is_cat:
                le = LabelEncoder()
                encoded = le.fit_transform(valid[col].cast(pl.Utf8).to_numpy())
                auc = roc_auc_score(y_true, encoded)
            else:
                auc = roc_auc_score(y_true, valid[col].to_numpy())
            auc = max(auc, 1 - auc)
            results.append((col, round(auc, 4)))
        except (ValueError, TypeError):
            continue
    return sorted(results, key=lambda x: -x[1])


def single_feature_rmse(df: pl.DataFrame, target_col: str, exclude: set) -> list[tuple[str, float, float]]:
    """Compute correlation-based RMSE reduction for regression."""
    results = []
    num_cols = get_numeric_cols(df, target_col, exclude)
    for col in num_cols:
        valid = df.select([col, target_col]).drop_nulls()
        if len(valid) < 10:
            continue
        corr = valid.select(pl.corr(col, target_col)).item()
        if corr is None or np.isnan(corr):
            continue
        reduction = 1 - np.sqrt(max(0, 1 - corr**2))
        results.append((col, round(abs(corr), 4), round(reduction * 100, 1)))
    return sorted(results, key=lambda x: -x[1])


def print_section(title: str, content: str):
    print(f"\n{'='*60}")
    print(f"[DATA_ANALYSIS] {title}")
    print(f"{'='*60}")
    print(content)


def main():
    parser = argparse.ArgumentParser(description="Pre-training data analysis")
    parser.add_argument("--config", default=None, help="Path to config YAML (e.g. conf/config.yaml)")
    parser.add_argument("--config-name", default=None,
                        help="Config name without extension (e.g. 'config' -> conf/config.yaml)")
    parser.add_argument("--top-k", type=int, default=20, help="Max features to show in rankings")
    parser.add_argument("--max-rows", type=int, default=1000000,
                        help="Max rows for analysis (0 = no limit)")
    args = parser.parse_args()

    if args.config:
        config_path = args.config
    elif args.config_name:
        config_path = f"conf/{args.config_name}.yaml"
    else:
        config_path = "conf/config.yaml"
    cfg = load_config(config_path)

    data_path = cfg.data.train_path
    target_col = cfg.data.target_column
    exclude = set(cfg.data.get("exclude_columns", []) or [])
    top_k = args.top_k
    max_rows = args.max_rows

    # Load
    print(f"Loading data from {data_path}...")
    df = load_data(data_path)
    print(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns")

    # Sample if too large
    if max_rows > 0 and len(df) > max_rows:
        print(f"Sampling {max_rows} rows from {len(df)} for analysis...")
        df = df.sample(n=max_rows, seed=42)

    if target_col not in df.columns:
        print(f"ERROR: target column '{target_col}' not found in data", file=sys.stderr)
        sys.exit(1)

    # Auto-detect task
    task = cfg.get("task", "auto")
    if task == "auto":
        n_unique = df[target_col].n_unique()
        if n_unique == 2:
            task = "binary"
        elif n_unique <= 20 and df[target_col].dtype in (pl.Int32, pl.Int64, pl.Utf8, pl.Categorical):
            task = "multiclass"
        else:
            task = "regression"
    print(f"Task: {task} (target '{target_col}', {df[target_col].n_unique()} unique values)")

    # 1. Feature stats
    stats_text, stats_rows = feature_stats(df, target_col, exclude)
    print_section("Feature Statistics", stats_text)

    # 2. Target distribution
    if task == "binary":
        vc = df[target_col].value_counts().sort("count", descending=True)
        total = len(df)
        lines = []
        for row in vc.iter_rows(named=True):
            val = row[target_col]
            cnt = row["count"]
            lines.append(f"  Class {val}: {cnt} ({cnt/total*100:.1f}%)")
        max_cnt = vc["count"].max()
        min_cnt = vc["count"].min()
        lines.append(f"  Imbalance ratio: {max_cnt/min_cnt:.2f}")
        print_section("Target Distribution", "\n".join(lines))
    elif task == "regression":
        t = df[target_col].drop_nulls()
        print_section("Target Distribution",
            f"  mean={t.mean():.4f}  std={t.std():.4f}  min={t.min():.4f}  max={t.max():.4f}\n"
            f"  skew={t.skew():.2f}  kurtosis={t.kurtosis():.2f}")

    # 3. Correlations (numeric)
    corrs = numeric_correlations(df, target_col, exclude)
    if corrs:
        lines = [f"  {name:30s} corr={c:+.4f}" for name, c in corrs[:top_k]]
        print_section("Numeric Feature-Target Correlations", "\n".join(lines))

    # 4. Categorical target rates
    cat_rates = categorical_target_rates(df, target_col, exclude, task)
    if cat_rates:
        lines = []
        for cr in cat_rates:
            lines.append(f"  {cr['feature']} ({cr['n_groups']} groups): "
                        f"target_rate_range={cr.get('target_rate_range', cr.get('target_mean_range', ''))}")
            if "top_groups" in cr:
                for g, r in list(cr["top_groups"].items())[:3]:
                    lines.append(f"    {g}: {r}")
        print_section("Categorical Feature-Target Rates", "\n".join(lines))

    # 5. Single-feature predictive power
    if task == "binary":
        auc_results = single_feature_auc(df, target_col, exclude)
        if auc_results:
            lines = [f"  {name:30s} AUC={auc:.4f}" for name, auc in auc_results[:top_k]]
            print_section("Single-Feature AUC (binary)", "\n".join(lines))
    elif task == "regression":
        rmse_results = single_feature_rmse(df, target_col, exclude)
        if rmse_results:
            lines = [f"  {name:30s} |corr|={c:.4f}  RMSE_reduction={r:.1f}%" for name, c, r in rmse_results[:top_k]]
            print_section("Single-Feature Predictive Power (regression)", "\n".join(lines))

    # 6. Feature engineering suggestions
    suggestions = []

    for _, dtype, missing_pct, n_unique, col in stats_rows:
        if missing_pct > 50:
            suggestions.append(f"- {col}: {missing_pct}% missing — consider has_feature binary flag or drop")
        if dtype == "cat" and n_unique > 50:
            suggestions.append(f"- {col}: {n_unique} unique values — consider grouping rare categories or encoding")

    # Skewed numerics
    num_cols = get_numeric_cols(df, target_col, exclude)
    for col in num_cols:
        skew_val = df[col].drop_nulls().skew()
        if skew_val is not None and abs(skew_val) > 2:
            suggestions.append(f"- {col}: skew={skew_val:.2f} — consider log/sqrt transform")

    # Low-predictive features
    if task == "binary":
        auc_results_local = single_feature_auc(df, target_col, exclude) if not corrs else auc_results
        low_auc = [(n, a) for n, a in auc_results_local if a < 0.52]
        if low_auc:
            names = [n for n, _ in low_auc]
            suggestions.append(f"- Low single-feature AUC (<0.52): {', '.join(names)} — candidate for removal or crossing with stronger features")

    if suggestions:
        print_section("Feature Engineering Suggestions", "\n".join(suggestions))
    else:
        print_section("Feature Engineering Suggestions", "  No obvious suggestions — features look reasonable.")

    print(f"\n{'='*60}")
    print("[DATA_ANALYSIS] Analysis complete.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
