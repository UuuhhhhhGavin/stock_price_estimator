
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
        
        # Create output JSON structure
        output = {
            'lastUpdated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'dataCount': len(data),
            'columns': list(df.columns),
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
