
"""
Convert Excel data to JSON format for GitHub Pages display
"""

import pandas as pd
import json
import os
from datetime import datetime


def convert_excel_to_json(excel_file, output_dir='docs'):
    """
    Convert Excel file to JSON and create necessary files for GitHub Pages
    
    Args:
        excel_file: Path to the Excel file
        output_dir: Output directory for JSON and HTML files
    """
    
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # Read Excel file
        df = pd.read_excel(excel_file)
        
        # Convert to JSON-friendly format
        # Handle any NaN values and datetime objects
        data = []
        for _, row in df.iterrows():
            row_dict = {}
            for col, val in row.items():
                # Convert NaN to None, datetime to string
                if pd.isna(val):
                    row_dict[col] = None
                elif isinstance(val, (pd.Timestamp, datetime)):
                    row_dict[col] = val.strftime('%Y-%m-%d %H:%M:%S')
                elif isinstance(val, float):
                    row_dict[col] = round(val, 4)
                else:
                    row_dict[col] = str(val) if not isinstance(val, (int, float, bool, type(None))) else val
            data.append(row_dict)
        
        # Create summary metadata for the website
        summary = {
            'avg_abs_error_pct': round(df['abs_error_pct'].dropna().mean(), 2) if 'abs_error_pct' in df.columns else None,
            'median_z_score': round(df['z_score'].dropna().median(), 2) if 'z_score' in df.columns else None,
            'directional_accuracy_pct': round((df['pdf_directional_correct'] == 1).mean() * 100, 2) if 'pdf_directional_correct' in df.columns else None,
            'interval_hit_rate_pct': round((df['landed_in_50_pct_interval'] == 1).mean() * 100, 2) if 'landed_in_50_pct_interval' in df.columns else None,
            'avg_atm_iv': round(df['ATM IV'].dropna().mean(), 4) if 'ATM IV' in df.columns else None,
            'avg_expected_std_pct': round(df['expected_std_pct'].dropna().mean(), 2) if 'expected_std_pct' in df.columns else None,
            # Confidence distribution (from analyzer output)
            'confidence_distribution': {
                'very_high': 4,  # 80-95%
                'high': 122,     # 60-80%
                'moderate': 162, # 50-60%
                'low': 206       # <50%
            },
            # Top 15 most confident predictions (placeholder - would need to integrate from analyzer)
            'top_confident_predictions': [
                # Example structure - replace with actual data
                {'ticker': 'NTRS', 'direction': 'up', 'confidence': 0.95, 'reliability': 1.0, 'price': 141.9, 'expected_price': 145.0, 'pct_change': 2.3, 'expiry': '2026-01-16'},
                # Add more...
            ],
            # Statistics from analyzer
            'statistics': {
                'normality_testing': {
                    'n': 8093,
                    'mean': -0.2990,
                    'median': -0.2002,
                    'std_dev': 1.5701,
                    'skewness': -1.4551,
                    'kurtosis': 26.0030,
                    'shapiro_wilk_p': 0.0,
                    'dagostino_k2_p': 0.0,
                    'anderson_darling_reject': True,
                    'jarque_bera_p': 0.0,
                    'kolmogorov_smirnov_p': 0.0
                },
                'tail_behavior': {
                    'beyond_z2_empirical': 11.95,
                    'beyond_z2_normal': 4.55,
                    'beyond_z3_empirical': 4.98,
                    'beyond_z3_normal': 0.27
                },
                'feature_correlations_reliability': [
                    {'feature': 'ATM Implied Volatility', 't_stat': 0.69, 'p_value': 0.4927},
                    {'feature': 'Expected Std Dev (% of price)', 't_stat': 0.12, 'p_value': 0.9032},
                    {'feature': 'Avg |Z-Score|', 't_stat': -5.82, 'p_value': 0.0},
                    # Add more...
                ],
                'feature_correlations_accuracy': [
                    {'feature': 'ATM Implied Volatility', 'r': 0.0597, 'p': 0.1863},
                    {'feature': 'Expected Std Dev (% of price)', 'r': 0.0033, 'p': 0.9414},
                    # Add more...
                ]
            }
        }

        # Create output JSON structure
        output = {
            'lastUpdated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'dataCount': len(data),
            'columns': list(df.columns),
            'summary': summary,
            'data': data
        }
        
        # Write to JSON file
        json_file = os.path.join(output_dir, 'data.json')
        with open(json_file, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✓ JSON file created: {json_file}")
        print(f"✓ Total records: {len(data)}")
        
        return json_file
        
    except Exception as e:
        print(f"✗ Error converting Excel to JSON: {e}")
        raise


if __name__ == '__main__':
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_dir = os.path.dirname(script_dir)
    excel_file = os.path.join(repo_dir, 'sp500_options_analysis.xlsx')
    
    if os.path.exists(excel_file):
        convert_excel_to_json(excel_file)
    else:
        print(f"✗ Excel file not found: {excel_file}")
