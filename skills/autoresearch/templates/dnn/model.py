"""
DNN model template — configurable MLP with categorical embeddings.
"""

import torch
import torch.nn as nn
from omegaconf import DictConfig


class DNNModel(nn.Module):
    def __init__(self, cfg: DictConfig, num_features: int, cat_cardinalities: dict[str, int]):
        """
        Args:
            cfg: Model config from Hydra (cfg.model section)
            num_features: Number of numerical input features
            cat_cardinalities: {feature_name: num_unique_values} for categorical features
        """
        super().__init__()
        model_cfg = cfg.model

        # Categorical embeddings
        self.embeddings = nn.ModuleDict()
        emb_total_dim = 0
        emb_dim = model_cfg.get("emb_dim", 8)
        for feat_name, cardinality in cat_cardinalities.items():
            dim = min(emb_dim, (cardinality + 1) // 2)
            self.embeddings[feat_name] = nn.Embedding(cardinality + 1, dim, padding_idx=0)
            emb_total_dim += dim

        input_dim = num_features + emb_total_dim
        hidden_units = list(model_cfg.hidden_units)
        dropout_rate = model_cfg.get("dropout", 0.0)
        use_batchnorm = model_cfg.get("batch_norm", False)
        output_dim = model_cfg.get("output_dim", 1)

        layers = []
        prev_dim = input_dim
        for h in hidden_units:
            layers.append(nn.Linear(prev_dim, h))
            if use_batchnorm:
                layers.append(nn.BatchNorm1d(h))
            layers.append(nn.ReLU())
            if dropout_rate > 0:
                layers.append(nn.Dropout(dropout_rate))
            prev_dim = h
        layers.append(nn.Linear(prev_dim, output_dim))

        self.tower = nn.Sequential(*layers)

    def forward(self, numerical: torch.Tensor, categorical: dict[str, torch.Tensor]) -> torch.Tensor:
        parts = [numerical]
        for feat_name, emb_layer in self.embeddings.items():
            if feat_name in categorical:
                parts.append(emb_layer(categorical[feat_name]))
        x = torch.cat(parts, dim=-1)
        return self.tower(x)
