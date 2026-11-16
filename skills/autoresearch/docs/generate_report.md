# Generate Report

After any loop exit, read the complete experiment history and referenced contracts through the selected mode (SQLite or research PR comments). Write `OUTPUT_DIR/REPORT.md` in workspace mode or a report comment in GitHub mode. Describe the model from the recorded kept commit. Explicitly mark missing baseline, metrics, or logs as unavailable. Include these sections:

### Takeaways

Some key short takeaways from the experiments include but are not limited to:
- The actual running status of the contract
- Key improvements achieved (baseline -> best, with specific metric deltas)
- What experiment directions worked vs. didn't (high-level patterns)
- ...

### Model Description

**What is being modeled:** Describe the prediction task, target variable, and business context (inferred from code and config).

**Samples & Features:** List input features grouped by type (e.g. numerical, categorical, sequential, etc.). Note the total count and any notable features (high-cardinality categoricals, derived features, etc.).

**Model Structure:** Describe the architecture — tower structure, layer sizes, activations, calibration mechanism, embeddings, etc. Include the current best hyperparameters from config. Output a figure of the model architecture if possible (e.g., using `torchviz` or a hand-drawn diagram).

### Performance

Create GFM tables or figures to summarize the performances of baseline and latest 3 kept experiments.

Baseline is the kept iteration-0 record; latest results are the highest-iteration kept records, without duplicating baseline. Compare compatible evaluation protocols only, label contract versions and metric directions, and distinguish absolute differences from relative changes.

Except of the key metrics in the contract, you can decide to also include any additional relevant metrics that provide insight into model performance and behavior. For example:
- Final train vs test metrics (to show overfitting/underfitting gap)
- Feature importance or SHAP values if available
- Any other diagnostics (confusion matrix, calibration stats, loss curve summary)
- User-defined metrics or any other custom evaluation criteria

Discuss metric trends: which metrics improved most, which are still challenging, any trade-offs observed.

### Iterating History

Render experiment records as a readable GFM table, one row per experiment, including iteration/ID, hypothesis, status, metrics, summary, and commit/evidence references. Do not embed raw JSON.

Summarize cross-experiment findings based on metrics and recorded observations, not status alone: `dropped` can mean a non-winning candidate, stale parent, or interrupted decision rather than an ineffective direction.

- **Directions that helped:** (e.g., "Simplifying the calibration tower improved convergence")
- **Directions that didn't help:** (e.g., "Increasing model capacity hurt due to limited epochs")
- **Key insights:** (e.g., "Learning rate is the most sensitive hyperparameter for this model")
