"""
Convert Excel data to JSON format for GitHub Pages display with Quantitative Trading Insights.
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import sys

# Add parent directory to path to import stock_analyzer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from stock_analyzer import run_zscore_analysis, run_accuracy_analysis, compute_quant_signals, compute_realized_metrics


def _compute_strategy_performance(realized_df, hit_1std):
    """Backtest sentiment long/short and VRP metrics from realized rows only."""
    perf = {
        'sentiment_win_rate_pct': None,
        'sentiment_long_short_alpha_pct': None,
        'sentiment_annualized_alpha_pct': None,
        'sentiment_sharpe_ratio': None,
        'premium_harvest_win_rate_pct': hit_1std,
        'option_overpricing_factor': None,
        'total_realized_evaluations': int(len(realized_df)),
    }
    needed = ['sentiment_score', 'current_price', 'realized_price']
    if realized_df.empty or not all(c in realized_df.columns for c in needed):
        return perf

    r = realized_df.dropna(subset=needed).copy()
    r = r[r['current_price'] > 0]
    if r.empty:
        return perf
    r['ret_pct'] = (r['realized_price'] - r['current_price']) / r['current_price'] * 100

    hi = r['sentiment_score'].quantile(0.8)
    lo = r['sentiment_score'].quantile(0.2)
    longs = r[r['sentiment_score'] >= hi]['ret_pct']
    shorts = -r[r['sentiment_score'] <= lo]['ret_pct']
    trades = pd.concat([longs, shorts])
    if len(trades) > 1 and trades.std() > 0:
        perf['sentiment_win_rate_pct'] = round(float((trades > 0).mean() * 100), 2)
        perf['sentiment_long_short_alpha_pct'] = round(float(trades.mean()), 3)
        perf['sentiment_annualized_alpha_pct'] = round(float(trades.mean() * 52), 1)
        perf['sentiment_sharpe_ratio'] = round(float(trades.mean() / trades.std() * np.sqrt(52)), 2)

    if 'expected_std_pct' in r.columns:
        exp_move = r['expected_std_pct'].dropna().mean() * 100
        act_move = r['ret_pct'].abs().mean()
        if act_move > 0 and pd.notna(exp_move):
            perf['option_overpricing_factor'] = round(float(exp_move / act_move), 2)
    return perf


def convert_excel_to_json(excel_file, output_dir='docs'):
    """
    Convert Excel file to JSON and create enriched data structures for the GitHub Pages dashboard.

    Args:
        excel_file: Path to the Excel file
        output_dir: Output directory for JSON and HTML files
    """
    os.makedirs(output_dir, exist_ok=True)

    try:
        print(f"Reading Excel log: {excel_file}...")
        df = pd.read_excel(excel_file)

        # Ensure quantitative signals and realized metrics are populated
        if 'realized_price' in df.columns and df['realized_price'].notna().any():
            df = compute_realized_metrics(df)

        if 'strategy_recommendation' not in df.columns or df['strategy_recommendation'].isna().all():
            print("Applying quantitative options strategy engine...")
            df = compute_quant_signals(df)

        # Convert to JSON-friendly records
        data = []
        for _, row in df.iterrows():
            row_dict = {}
            for col, val in row.items():
                if pd.isna(val):
                    row_dict[col] = None
                elif isinstance(val, (pd.Timestamp, datetime)):
                    row_dict[col] = val.strftime('%Y-%m-%d %H:%M:%S')
                elif isinstance(val, (float, np.floating)):
                    row_dict[col] = round(float(val), 4)
                elif isinstance(val, (int, np.integer)):
                    row_dict[col] = int(val)
                elif isinstance(val, (bool, np.bool_)):
                    row_dict[col] = bool(val)
                else:
                    row_dict[col] = str(val)
            data.append(row_dict)

        # Run analysis functions for model calibration statistics
        print("Running z-score analysis...")
        zscore_stats = run_zscore_analysis(excel_file)

        print("Running accuracy analysis...")
        accuracy_stats = run_accuracy_analysis(excel_file)

        # Subsets for accurate metric calculations
        realized_df = df[df['pdf_directional_correct'].notna()].copy()
        pending_df = df[df['realized_price'].isna()].copy()
        if pending_df.empty:
            pending_df = df.sort_values('date', ascending=False).drop_duplicates(subset=['ticker'], keep='first')
        else:
            pending_df = pending_df.sort_values('date', ascending=False).drop_duplicates(subset=['ticker'], keep='first')

        # Realized performance metrics (avoiding dilution by unexpired rows)
        dir_acc = round(float(realized_df['pdf_directional_correct'].mean() * 100), 2) if not realized_df.empty else None
        hit_50 = round(float(realized_df['landed_in_50_pct_interval'].mean() * 100), 2) if (not realized_df.empty and 'landed_in_50_pct_interval' in realized_df) else None
        hit_1std = round(float(realized_df['landed_in_1std_interval'].mean() * 100), 2) if (not realized_df.empty and 'landed_in_1std_interval' in realized_df and realized_df['landed_in_1std_interval'].notna().any()) else None

        # Confidence distribution computed dynamically from pending predictions
        conf_series = pending_df['indicator_confidence'].dropna() if 'indicator_confidence' in pending_df.columns else pd.Series()
        confidence_distribution = {
            'very_high': int((conf_series >= 80).sum()),
            'high': int(((conf_series >= 60) & (conf_series < 80)).sum()),
            'moderate': int(((conf_series >= 50) & (conf_series < 60)).sum()),
            'low': int((conf_series < 50).sum())
        }

        # Strategy allocation distribution
        strat_series = pending_df['strategy_recommendation'].dropna() if 'strategy_recommendation' in pending_df.columns else pd.Series()
        strategy_distribution = strat_series.value_counts().to_dict()

        # Helper to format picks
        def _format_pick(r):
            exp_date = r.get('analyzed option expiration')
            if isinstance(exp_date, (pd.Timestamp, datetime)):
                exp_str = exp_date.strftime('%Y-%m-%d')
            else:
                exp_str = str(exp_date).split(' ')[0] if exp_date else None

            return {
                'ticker': r.get('ticker'),
                'direction': r.get('indicator_direction'),
                'strategy': r.get('strategy_recommendation'),
                'confidence': round(float(r.get('indicator_confidence', 50.0)), 1),
                'sentiment_score': round(float(r.get('sentiment_score', 0.5)), 3) if pd.notna(r.get('sentiment_score')) else 0.5,
                'reliability': r.get('indicator_reliability', 'MODERATE'),
                'price': round(float(r.get('current_price')), 2) if pd.notna(r.get('current_price')) else None,
                'expected_price': round(float(r.get('expected_price')), 2) if pd.notna(r.get('expected_price')) else None,
                'pct_change': round(float(r.get('percent change %')), 1) if pd.notna(r.get('percent change %')) else None,
                'expected_std_pct': round(float(r.get('expected_std_pct', 0) * 100), 1) if pd.notna(r.get('expected_std_pct')) else None,
                'atm_iv': round(float(r.get('ATM IV', 0) * 100), 1) if pd.notna(r.get('ATM IV')) else None,
                'iv_skew': round(float(r.get('iv_skew', 0)), 3) if pd.notna(r.get('iv_skew')) else None,
                'pcr_volume': round(float(r.get('pcr_volume', 0)), 2) if pd.notna(r.get('pcr_volume')) else None,
                'actionable_setup': r.get('actionable_setup'),
                'expiry': exp_str,
                'p25': round(float(r.get('p25')), 2) if pd.notna(r.get('p25')) else None,
                'p75': round(float(r.get('p75')), 2) if pd.notna(r.get('p75')) else None
            }

        # Dynamic Top Strategy Picks
        bulls = pending_df[pending_df.get('strategy_recommendation') == 'BULLISH FLOW'].sort_values('sentiment_score', ascending=False)
        bears = pending_df[pending_df.get('strategy_recommendation') == 'BEARISH FLOW'].sort_values('sentiment_score', ascending=True)
        harvest = pending_df[pending_df.get('strategy_recommendation') == 'PREMIUM HARVEST'].sort_values('expected_std_pct', ascending=False)

        top_bullish_picks = [_format_pick(row) for _, row in bulls.head(15).iterrows()]
        top_bearish_picks = [_format_pick(row) for _, row in bears.head(15).iterrows()]
        top_premium_harvest_picks = [_format_pick(row) for _, row in harvest.head(15).iterrows()]

        # Top 15 confident predictions across all strategies
        top_confident = pending_df.sort_values('indicator_confidence', ascending=False).head(15)
        top_confident_predictions = [_format_pick(row) for _, row in top_confident.iterrows()]

        strategy_performance = _compute_strategy_performance(realized_df, hit_1std)

        # Build complete summary dictionary
        summary = {
            'avg_abs_error_pct': round(float(df['abs_error_pct'].dropna().mean()), 2) if 'abs_error_pct' in df.columns else None,
            'median_z_score': round(float(df['z_score'].dropna().median()), 2) if 'z_score' in df.columns else None,
            'directional_accuracy_pct': dir_acc,
            'interval_hit_rate_pct': hit_50,
            'std1_interval_hit_rate_pct': hit_1std,
            'avg_atm_iv': round(float(df['ATM IV'].dropna().mean()), 4) if 'ATM IV' in df.columns else None,
            'avg_expected_std_pct': round(float(df['expected_std_pct'].dropna().mean()), 2) if 'expected_std_pct' in df.columns else None,
            'confidence_distribution': confidence_distribution,
            'strategy_distribution': strategy_distribution,
            'strategy_performance': strategy_performance,
            'top_confident_predictions': top_confident_predictions,
            'top_bullish_picks': top_bullish_picks,
            'top_bearish_picks': top_bearish_picks,
            'top_premium_harvest_picks': top_premium_harvest_picks,
            'statistics': {}
        }

        if zscore_stats:
            summary['statistics'].update(zscore_stats)
        if accuracy_stats:
            summary['statistics'].update(accuracy_stats)

        output = {
            'lastUpdated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'dataCount': len(data),
            'columns': list(df.columns),
            'summary': summary,
            'data': data
        }

        json_file = os.path.join(output_dir, 'data.json')
        with open(json_file, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"[OK] JSON file created: {json_file}")
        print(f"[OK] Total records: {len(data)}")
        print(f"[OK] Top Bullish Picks: {len(top_bullish_picks)}")
        print(f"[OK] Top Bearish Picks: {len(top_bearish_picks)}")
        print(f"[OK] Top Premium Harvest Picks: {len(top_premium_harvest_picks)}")

        return json_file

    except Exception as e:
        print(f"[ERROR] Error converting Excel to JSON: {e}")
        raise


if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_dir = os.path.dirname(script_dir)
    excel_file = os.path.join(repo_dir, 'sp500_options_analysis.xlsx')

    if os.path.exists(excel_file):
        convert_excel_to_json(excel_file)
    else:
        print(f"[ERROR] Excel file not found: {excel_file}")
