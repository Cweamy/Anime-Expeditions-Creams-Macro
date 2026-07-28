import os
import re
import pytest

def test_log_line_classification_logic_present():
    """
    Test that ui/log_view.js contains the logic to classify [Diagnostics]
    and [Preflight] log tags with the 'log-badge-diagnostic' CSS class.
    """
    ui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    log_view_path = os.path.join(ui_dir, "log_view.js")
    
    assert os.path.exists(log_view_path), f"log_view.js not found at {log_view_path}"
    
    with open(log_view_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Check that both Diagnostics and Preflight are being checked
    assert "Diagnostics" in content, "Diagnostics tag not found in log_view.js"
    assert "Preflight" in content, "Preflight tag not found in log_view.js"
    
    # Check that the CSS class is applied
    assert "log-badge-diagnostic" in content, "CSS class 'log-badge-diagnostic' not found in log_view.js"
    
    # Optional: more strict regex check to ensure the logic exists
    # Something like: if (tagName === 'Diagnostics' || tagName === 'Preflight')
    # or similar constructs
    pattern = re.compile(r"tagName\s*===\s*['\"]Diagnostics['\"]\s*\|\|\s*tagName\s*===\s*['\"]Preflight['\"]")
    assert pattern.search(content) is not None, "Classification condition for Diagnostics or Preflight missing"
