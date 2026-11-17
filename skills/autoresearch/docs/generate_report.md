# Generate Report

After any loop exit, generate a report summarizing the experiments. Write `OUTPUT_DIR/REPORT.md` in workspace mode or a report comment in GitHub mode. The principal is to provide a human-friendly summary of the experiment outcomes, model behavior, and key insights. It should at least cover takeaways, model card, baseline vs best comparison, and iteration history as below, but any other information are encouraged to be included if available and relevant.

### Takeaways

A few key highlights or takeaways from the experiments.

### Model Card

**Task & Context:** Describe the modeling target given what inputs, and business context if available.

**Samples & Features:** List input features grouped by type (e.g. numerical, categorical, sequential, etc.). Note the total count and any notable features (high-cardinality categorical, derived features, etc.).

**Model Structure:** Describe the model architecture with relevant hyper-parameters as Figures or diagrams. Have a clear depiction of the model's components and their interactions.

### Baseline vs Best

Create GFM tables or plots to compare the performances between baseline (iteration-0) and the best experiment on all available metrics.

Except of the key metrics in the contract, you can decide to also include any additional relevant metrics that provide insight into model performance and behavior. For example:
- Final train vs test metrics (to show overfitting/underfitting gap)
- Feature importance or SHAP values if available
- Any other diagnostics (confusion matrix, calibration stats, loss curve summary)
- User-defined metrics or any other custom evaluation criteria

Summarize the key observations from the performance comparison, highlighting improvements, remaining challenges, and any trade-offs observed.

### Iterations

Render experiment records as a human-friendly table for tracking, one row per experiment, and contains all relevant details for each experiment, including iteration/ID, hypothesis, status, metrics, summary, and commit/evidence references etc.

Summarize cross-experiment findings based on metrics and recorded observations, not status alone: `dropped` can mean a non-winning candidate, stale parent, or interrupted decision rather than an ineffective direction.

- **Directions that helped:** (e.g., "Simplifying the calibration tower improved convergence")
- **Directions that didn't help:** (e.g., "Increasing model capacity hurt due to limited epochs")
- **Key insights:** (e.g., "Learning rate is the most sensitive hyperparameter for this model")
