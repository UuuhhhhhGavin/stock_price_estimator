# S&P 500 Options Analysis Dashboard - GitHub Pages

This directory contains the GitHub Pages website for displaying the S&P 500 options analysis data.

## Files

- **index.html** - Interactive dashboard for viewing the options data
- **data.json** - Generated JSON data file (auto-created by GitHub Actions)

## How It Works

1. **Data Generation**: The GitHub Actions workflow runs `github_action_stock_pdf_tracking.py` to generate the `sp500_options_analysis.xlsx` file
2. **JSON Conversion**: The `scripts/convert_excel_to_json.py` script converts the Excel file to `docs/data.json`
3. **Deployment**: The `peaceiris/actions-gh-pages@v3` action deploys the `docs/` directory to the `gh-pages` branch
4. **Hosting**: GitHub Pages serves the website from the `gh-pages` branch at `https://[your-username].github.io/[repo-name]/`

## Local Testing

To test the dashboard locally:

1. Generate the JSON data:
   ```bash
   python scripts/convert_excel_to_json.py
   ```

2. Open `docs/index.html` in your browser or serve it with a local server:
   ```bash
   # Using Python 3
   python -m http.server 8000
   ```

3. Visit `http://localhost:8000` in your browser

## Features

- **Search & Filter**: Search across all columns
- **Sorting**: Click column headers to sort
- **Pagination**: Navigate through large datasets
- **Download**: Export filtered data as JSON
- **Responsive Design**: Mobile-friendly interface
- **Real-time Stats**: View record count and update time

## GitHub Pages Configuration

The site is configured to deploy from the `gh-pages` branch. No additional setup is needed—the GitHub Actions workflow handles everything automatically.

To verify your GitHub Pages settings:
1. Go to your repository Settings
2. Navigate to Pages
3. Confirm the source is set to "Deploy from a branch"
4. Verify the branch is "gh-pages"

## Column Headers

Update the converter script to customize how columns are named and formatted in the dashboard.

## Troubleshooting

- **Data not updating**: Check the GitHub Actions workflow runs in your repository
- **Page not loading**: Verify the `gh-pages` branch exists and contains the `docs/` files
- **JSON not found**: Ensure `scripts/convert_excel_to_json.py` runs without errors
