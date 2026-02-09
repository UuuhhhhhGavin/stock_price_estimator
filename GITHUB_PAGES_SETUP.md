# GitHub Pages Setup Guide - S&P 500 Options Analysis

This guide walks you through setting up GitHub Pages for your stock options analysis dashboard.

## Overview

Your GitHub Pages site will automatically update every time the GitHub Actions workflow runs and generates new options data.

**Data Flow:**
```
1. GitHub Actions runs daily (3:30 CST)
   ↓
2. Runs github_action_stock_pdf_tracking.py
   ↓
3. Updates sp500_options_analysis.xlsx
   ↓
4. Converts Excel to docs/data.json
   ↓
5. Deploys to GitHub Pages (gh-pages branch)
   ↓
6. Website automatically updates
```

## What Was Added

### New Files Created:

1. **`docs/index.html`** - Interactive dashboard with:
   - Real-time data display
   - Search and filter capabilities
   - Sortable columns
   - Pagination
   - Download as JSON
   - Mobile-responsive design

2. **`scripts/convert_excel_to_json.py`** - Python script that:
   - Reads the Excel file
   - Converts to JSON format
   - Handles data type conversions
   - Creates `docs/data.json`

3. **`docs/data.json`** - Auto-generated JSON data file
   - Updated after each workflow run
   - Contains all Excel data in JSON format

4. **Updated `.github/workflows/python-app.yml`**:
   - Added JSON generation step
   - Added GitHub Pages deployment step using peaceiris/actions-gh-pages

5. **Updated `requirements.txt`**:
   - Added `openpyxl` for Excel file reading

## Configuration Steps

### Step 1: Enable GitHub Pages

1. Go to your repository on GitHub
2. Click **Settings** → **Pages**
3. Under "Source", select:
   - Branch: `gh-pages`
   - Folder: `/ (root)`
4. Click **Save**

### Step 2: Verify the Workflow

1. Go to **Actions** tab in your repository
2. Check the "Python application" workflow
3. Wait for the next scheduled run (or manually trigger it)
4. Verify that:
   - ✅ Python script runs successfully
   - ✅ JSON generation completes
   - ✅ GitHub Pages deployment succeeds

### Step 3: Access Your Site

Your GitHub Pages site will be available at:
```
https://[your-github-username].github.io/[repository-name]/
```

For example:
```
https://gavin.github.io/stock_price_estimator/
```

## Testing Locally

Before pushing, test the dashboard locally:

1. **Generate the JSON:**
   ```bash
   python scripts/convert_excel_to_json.py
   ```

2. **Serve the site:**
   ```bash
   # Using Python 3
   python -m http.server 8000
   
   # Or using Node.js http-server
   npx http-server docs
   ```

3. **View in browser:**
   ```
   http://localhost:8000
   ```

## Dashboard Features

The interactive dashboard includes:

### Search & Filter
- Search across all columns simultaneously
- Real-time filtering as you type

### Sorting
- Click any column header to sort
- Works with numbers, dates, and text
- Null values handled gracefully

### Pagination
- Choose records per page (25, 50, 100, 500)
- Navigate with Previous/Next buttons
- Jump to specific page

### Statistics
- Total record count
- Unique stock count
- Last update timestamp

### Export
- Download filtered data as JSON
- Timestamped filename

### Responsive Design
- Works on desktop, tablet, mobile
- Touch-friendly controls
- Optimized performance

## Troubleshooting

### Issue: Page not loading

**Solution:**
1. Check GitHub Pages is enabled in Settings
2. Verify `gh-pages` branch exists in your repository
3. Check Pages are configured to deploy from `gh-pages` branch
4. Wait 5-10 minutes for initial deployment

### Issue: Data not showing

**Solution:**
1. Check the GitHub Actions workflow ran successfully
2. Verify `docs/data.json` exists in the `gh-pages` branch
3. Open browser DevTools (F12) → Console tab
4. Check for error messages loading `data.json`

### Issue: JSON file not generated

**Solution:**
1. Ensure `openpyxl` is installed: `pip install openpyxl`
2. Run manually: `python scripts/convert_excel_to_json.py`
3. Check the Excel file path is correct
4. Verify Excel file has data in it

### Issue: Workflow fails to deploy

**Solution:**
1. Check workflow permissions: Settings → Actions → General
2. Ensure `contents: write` is in permissions
3. Check GitHub token is not expired
4. Review workflow logs for error details

## Customization

### Change Update Frequency

Edit `.github/workflows/python-app.yml`:
```yaml
schedule:
  - cron: "30 21 * * *"  # Change this cron schedule
```

Common schedules:
- `"0 9 * * *"` - Every day at 9 AM UTC
- `"0 12 * * *"` - Every day at 12 PM UTC
- `"0 * * * *"` - Every hour
- `"0 0 * * 0"` - Every Sunday at midnight

### Customize Dashboard Appearance

Edit `docs/index.html`:
- Change colors in the `<style>` section
- Modify the header text
- Add your logo or branding
- Add additional statistics

### Filter Specific Columns

Edit `scripts/convert_excel_to_json.py`:
```python
# Customize which columns to include
columns_to_include = ['Symbol', 'Strike', 'Probability', ...]
data = [
    {col: row[col] for col in columns_to_include}
    for _, row in df.iterrows()
]
```

## GitHub Actions Permissions

Make sure your repository has the correct permissions. In `.github/workflows/python-app.yml`:

```yaml
permissions:
  contents: write  # Allows writing to gh-pages branch
```

## Custom Domain (Optional)

To use a custom domain:

1. Create a `CNAME` file in `docs/` with your domain:
   ```
   yourdomain.com
   ```

2. Configure your domain DNS to point to GitHub Pages

3. Update the workflow file to include:
   ```yaml
   - name: Deploy to GitHub Pages
     uses: peaceiris/actions-gh-pages@v3
     with:
       github_token: ${{ secrets.GITHUB_TOKEN }}
       publish_dir: ./docs
       cname: yourdomain.com
   ```

## Additional Resources

- [GitHub Pages Documentation](https://docs.github.com/en/pages)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [peaceiris/actions-gh-pages](https://github.com/peaceiris/actions-gh-pages)

## Next Steps

1. ✅ Push all changes to your repository
2. ✅ Verify GitHub Pages is enabled
3. ✅ Wait for the next workflow run
4. ✅ Visit your GitHub Pages URL
5. ✅ Confirm the dashboard displays your data

---

## Questions or Issues?

Check the GitHub Actions workflow logs for detailed error messages:
1. Go to **Actions** tab
2. Click on the workflow run
3. Expand "Deploy to GitHub Pages" step
4. Review the output for error details
