# On-demand Data Analysis

## When to Use

- Use data analysis when you need to understand your dataset, or there are changes in the data or features that require investigation before or during model development. 
- If there are no historical data analysis related reports and you need to understand the dataset, consider generating one.

## Customizing the Script

Create or update Python or ad hoc SQL for data analysis. The skill's bundled `templates/analyze_data.py` is an optional starting point; adapt it to the target project's data sources and dependencies. Use the selected mode to edit and execute analysis code, and retain results in its evidence location. GitHub mode runs analysis through the configured remote execution path, not a presumed local shell.

**You can and should edit analysis code** to add domain-specific analysis as the experiment progresses. For example:
- Descriptive statistics for features (mean, median, std, missing values, etc.)
- Calculate correlations between features and target
- Cross tabulations or interactions between features
- Visualizations such as distribution plots, scatter plots, or histograms
- Any other domain-specific analyses that help understand the data or model performance
- ...
