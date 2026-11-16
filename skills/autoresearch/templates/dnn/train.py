"""
DNN training script for autoresearch.

Usage:
  python train.py --config-name config
  python train.py --config-name config train.epochs=10 model.hidden_units=[128,64]
"""

import logging
import os
import time

import hydra
import numpy as np
import torch
import torch.nn as nn
from omegaconf import DictConfig, OmegaConf
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

from common import compute_metrics, get_task, load_data, log_metrics, prepare_features
from model import DNNModel

log = logging.getLogger(__name__)


def get_criterion(cfg: DictConfig, task: str) -> nn.Module:
    """Select loss function based on task type."""
    if task == "binary":
        return nn.BCEWithLogitsLoss()
    elif task == "multiclass":
        return nn.CrossEntropyLoss()
    else:
        loss_name = cfg.train.get("loss_fn", "mse")
        return {"mse": nn.MSELoss, "mae": nn.L1Loss, "l1": nn.L1Loss, "huber": nn.HuberLoss}.get(
            loss_name, nn.MSELoss
        )()


def train_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: torch.device,
    cat_cols: list[str],
    task: str,
    gradient_clip_norm: float | None = None,
) -> float:
    model.train()
    total_loss = 0.0
    n_batches = 0
    for batch in loader:
        *parts, labels = batch
        numerical = parts[0].to(device)
        cat_dict = {col: parts[i + 1].to(device) for i, col in enumerate(cat_cols)}
        labels = labels.to(device)

        optimizer.zero_grad()
        output = model(numerical, cat_dict)
        if task in ("regression", "binary"):
            output = output.squeeze(-1)
        loss = criterion(output, labels)
        loss.backward()
        if gradient_clip_norm:
            nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
        optimizer.step()

        total_loss += loss.item()
        n_batches += 1

    return total_loss / max(n_batches, 1)


@torch.no_grad()
def evaluate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    cat_cols: list[str],
    task: str,
) -> dict:
    model.eval()
    all_preds = []
    all_probs = []
    all_labels = []
    total_loss = 0.0
    n_batches = 0

    for batch in loader:
        *parts, labels = batch
        numerical = parts[0].to(device)
        cat_dict = {col: parts[i + 1].to(device) for i, col in enumerate(cat_cols)}
        labels = labels.to(device)

        output = model(numerical, cat_dict)
        if task in ("regression", "binary"):
            output = output.squeeze(-1)
        loss = criterion(output, labels)

        total_loss += loss.item()
        n_batches += 1

        if task == "binary":
            probs = torch.sigmoid(output).cpu().numpy()
            preds = (probs >= 0.5).astype(np.int64)
            all_probs.append(probs)
            all_preds.append(preds)
        elif task == "multiclass":
            probs = torch.softmax(output, dim=-1).cpu().numpy()
            preds = probs.argmax(axis=-1)
            all_probs.append(probs)
            all_preds.append(preds)
        else:
            all_preds.append(output.cpu().numpy())

        all_labels.append(labels.cpu().numpy())

    preds = np.concatenate(all_preds)
    labels = np.concatenate(all_labels)
    probs = np.concatenate(all_probs) if all_probs else None
    metrics = compute_metrics(preds, labels, task=task, probs=probs)
    metrics["loss"] = total_loss / max(n_batches, 1)
    return metrics


@hydra.main(config_path="conf", config_name="config", version_base=None)
def main(cfg: DictConfig):
    log.info(f"Starting training with configs:\n{OmegaConf.to_yaml(cfg)}")

    task = get_task(cfg)
    log.info(f"Task type: {task}")

    device = torch.device("cuda" if cfg.train.get("use_gpu", False) and torch.cuda.is_available() else "cpu")
    log.info(f"Using device: {device}")

    seed = cfg.train.get("random_seed", 42)
    torch.manual_seed(seed)
    np.random.seed(seed)

    # Load data
    log.info(f"Loading data from {cfg.data.train_path}")
    df = load_data(cfg.data.train_path)
    log.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")

    numerical, categorical, labels, label_encoders, num_cols, cat_cols = prepare_features(df, cfg)
    log.info(f"Features: {len(num_cols)} numerical, {len(cat_cols)} categorical")

    # Auto-detect output_dim for classification
    if task == "multiclass":
        num_classes = cfg.model.get("num_classes") or int(labels.max()) + 1
        OmegaConf.update(cfg, "model.output_dim", num_classes)
        log.info(f"Multiclass: {num_classes} classes")
    elif task == "binary":
        OmegaConf.update(cfg, "model.output_dim", 1)

    # Train/test split
    test_frac = cfg.train.get("test_frac", 0.2)
    indices = np.arange(len(labels))
    stratify = labels if task in ("binary", "multiclass") else None
    train_idx, test_idx = train_test_split(indices, test_size=test_frac, random_state=seed, stratify=stratify)

    def make_tensors(idx):
        parts = [torch.tensor(numerical[idx])]
        for col in cat_cols:
            parts.append(torch.tensor(categorical[col][idx]))
        if task == "multiclass":
            parts.append(torch.tensor(labels[idx], dtype=torch.long))
        elif task == "binary":
            parts.append(torch.tensor(labels[idx], dtype=torch.float32))
        else:
            parts.append(torch.tensor(labels[idx]))
        return parts

    batch_size = cfg.train.get("batch_size", 64)
    train_loader = DataLoader(TensorDataset(*make_tensors(train_idx)), batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(TensorDataset(*make_tensors(test_idx)), batch_size=batch_size * 4, shuffle=False)

    # Build model
    cat_cardinalities = {col: len(le.classes_) + 1 for col, le in label_encoders.items()}
    model = DNNModel(cfg, num_features=numerical.shape[1], cat_cardinalities=cat_cardinalities).to(device)
    log.info(f"Model parameters: {sum(p.numel() for p in model.parameters()):,}")

    # Optimizer
    opt_cfg = cfg.train.optimizer
    lr = opt_cfg.get("learning_rate", 1e-3)
    wd = opt_cfg.get("weight_decay", 1e-4)
    if opt_cfg.get("type", "adamw") == "adamw":
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    else:
        optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=wd)

    # LR scheduler
    scheduler = None
    if "lr_scheduler" in cfg.train:
        sched_cfg = cfg.train.lr_scheduler
        sched_type = sched_cfg.get("type", "step_lr")
        if sched_type == "step_lr":
            scheduler = torch.optim.lr_scheduler.StepLR(
                optimizer, step_size=sched_cfg.get("step_size", 5), gamma=sched_cfg.get("gamma", 0.5),
            )
        elif sched_type == "cosine":
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=cfg.train.epochs)

    # Loss
    criterion = get_criterion(cfg, task)

    # Training loop
    epochs = cfg.train.get("epochs", 50)
    timeout_seconds = cfg.train.get("timeout_seconds", None)
    patience = cfg.train.get("early_stopping_patience", 10)
    gradient_clip_norm = cfg.train.get("gradient_clip_norm", None)

    best_val_loss = float("inf")
    best_state = None
    epochs_no_improve = 0
    t_start = time.time()

    for epoch in range(1, epochs + 1):
        train_loss = train_epoch(model, train_loader, optimizer, criterion, device, cat_cols, task, gradient_clip_norm)
        metrics = evaluate(model, test_loader, criterion, device, cat_cols, task)
        if scheduler is not None:
            scheduler.step()
        current_lr = optimizer.param_groups[0]["lr"]

        metric_str = "  ".join(f"{k}={v:.4f}" for k, v in metrics.items() if k != "loss")
        elapsed = time.time() - t_start
        log.info(
            f"Epoch {epoch}/{epochs}  train_loss={train_loss:.6f}  "
            f"val_loss={metrics['loss']:.6f}  {metric_str}  lr={current_lr:.2e}  elapsed={elapsed:.0f}s"
        )

        # Early stopping with best-model checkpoint
        if metrics["loss"] < best_val_loss:
            best_val_loss = metrics["loss"]
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if patience and epochs_no_improve >= patience:
                log.info(f"Early stopping: no improvement for {patience} epochs (best val_loss={best_val_loss:.6f})")
                break

        if timeout_seconds and elapsed >= timeout_seconds:
            log.info(f"Timeout reached ({elapsed:.0f}s >= {timeout_seconds}s) after epoch {epoch}/{epochs}. Stopping.")
            break

    # Restore best model
    if best_state is not None:
        model.load_state_dict(best_state)
        log.info(f"Restored best model (val_loss={best_val_loss:.6f})")

    # Final metrics — both train and test for overfitting analysis
    final_test = evaluate(model, test_loader, criterion, device, cat_cols, task)
    final_train = evaluate(model, train_loader, criterion, device, cat_cols, task)
    log.info("=" * 60)
    log.info("Final test metrics:")
    log_metrics(final_test)

    # Overfitting analysis
    log.info("=" * 60)
    log.info("[DIAGNOSTIC] Train vs Test gap (overfitting check):")
    for key in final_test:
        if key in final_train:
            gap = final_train[key] - final_test[key]
            log.info(f"  {key}: train={final_train[key]:.4f}  test={final_test[key]:.4f}  gap={gap:+.4f}")

    # Per-class metrics for classification
    if task == "binary":
        from sklearn.metrics import confusion_matrix as cm_func
        # Re-run to get predictions for confusion matrix
        all_preds, all_labels_arr = [], []
        with torch.no_grad():
            for batch in test_loader:
                *parts, batch_labels = batch
                num_t = parts[0].to(device)
                cat_d = {col: parts[i + 1].to(device) for i, col in enumerate(cat_cols)}
                output = model(num_t, cat_d).squeeze(-1)
                pred_cls = (torch.sigmoid(output) >= 0.5).cpu().numpy().astype(np.int64)
                all_preds.append(pred_cls)
                all_labels_arr.append(batch_labels.numpy())
        preds_arr = np.concatenate(all_preds)
        labels_arr = np.concatenate(all_labels_arr)
        cm = cm_func(labels_arr, preds_arr)
        tn, fp, fn, tp = cm.ravel()
        log.info("[DIAGNOSTIC] Confusion matrix:")
        log.info(f"  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
        log.info(f"  False positive rate: {fp/(fp+tn):.4f}  False negative rate: {fn/(fn+tp):.4f}")

    final = final_test

    # Save model
    output_dir = cfg.train.get("output_dir", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    model_path = os.path.join(output_dir, "model.pt")
    torch.save(model.state_dict(), model_path)
    log.info(f"Model saved to {model_path}")


if __name__ == "__main__":
    main()
