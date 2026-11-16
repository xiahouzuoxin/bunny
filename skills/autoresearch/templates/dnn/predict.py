"""
DNN prediction / evaluation script.

Usage:
  python predict.py --config-name config
  python predict.py --config-name config predict.input_data=path/to/test.parquet
"""

import logging

import hydra
import numpy as np
import torch
from omegaconf import DictConfig, OmegaConf
from torch.utils.data import DataLoader, TensorDataset

from common import compute_metrics, get_task, load_data, log_metrics, prepare_features
from model import DNNModel

log = logging.getLogger(__name__)


@hydra.main(config_path="conf", config_name="config", version_base=None)
def main(cfg: DictConfig):
    log.info(f"Starting prediction with configs:\n{OmegaConf.to_yaml(cfg)}")

    task = get_task(cfg)
    device = torch.device("cuda" if cfg.train.get("use_gpu", False) and torch.cuda.is_available() else "cpu")

    data_path = cfg.predict.get("input_data") or cfg.data.train_path
    log.info(f"Loading data from {data_path}")
    df = load_data(data_path)

    numerical, categorical, labels, label_encoders, num_cols, cat_cols = prepare_features(df, cfg)

    # Auto-detect output_dim for multiclass
    if task == "multiclass":
        num_classes = cfg.model.get("num_classes") or int(labels.max()) + 1
        OmegaConf.update(cfg, "model.output_dim", num_classes)

    cat_cardinalities = {col: len(le.classes_) + 1 for col, le in label_encoders.items()}
    model = DNNModel(cfg, num_features=numerical.shape[1], cat_cardinalities=cat_cardinalities).to(device)

    model_path = cfg.predict.get("model_path", "outputs/model.pt")
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()
    log.info(f"Loaded model from {model_path}")

    parts = [torch.tensor(numerical)]
    for col in cat_cols:
        parts.append(torch.tensor(categorical[col]))
    parts.append(torch.tensor(labels))

    batch_size = cfg.predict.get("batch_size", 4096)
    loader = DataLoader(TensorDataset(*parts), batch_size=batch_size, shuffle=False)

    all_preds = []
    all_probs = []
    all_labels = []
    with torch.no_grad():
        for batch in loader:
            *batch_parts, batch_labels = batch
            num_tensor = batch_parts[0].to(device)
            cat_dict = {col: batch_parts[i + 1].to(device) for i, col in enumerate(cat_cols)}
            output = model(num_tensor, cat_dict)

            if task == "binary":
                output = output.squeeze(-1)
                probs = torch.sigmoid(output).cpu().numpy()
                preds = (probs >= 0.5).astype(np.int64)
                all_probs.append(probs)
            elif task == "multiclass":
                probs = torch.softmax(output, dim=-1).cpu().numpy()
                preds = probs.argmax(axis=-1)
                all_probs.append(probs)
            else:
                preds = output.squeeze(-1).cpu().numpy()

            all_preds.append(preds)
            all_labels.append(batch_labels.numpy())

    preds = np.concatenate(all_preds)
    labels_arr = np.concatenate(all_labels)
    probs = np.concatenate(all_probs) if all_probs else None

    metrics = compute_metrics(preds, labels_arr, task=task, probs=probs)
    metrics["n_samples"] = len(labels_arr)
    log.info("Evaluation results:")
    log_metrics(metrics)


if __name__ == "__main__":
    main()
