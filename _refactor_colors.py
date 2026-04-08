import re

file_path = r"d:\Sprint Health Script\sprint_health_2.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Define mapping from old hardcoded colors to new semantic variables
color_map = {
    r"#1a6bff": "var(--ant-primary-500)",
    r"#00d4aa": "var(--success-main)",
    r"#ff4757": "var(--error-main)",
    r"#fbbf24": "var(--warning-main)",
    r"#fa8c16": "var(--warning-main)", # Mapping orange to warning for now or standard orange
    r"#fb923c": "var(--warning-main)",
    r"#4a90d9": "var(--text-soft)",
    r"#8ab4d9": "var(--text-soft)",
    r"#e0eaff": "var(--text-main)",
    r"#f5f8ff": "var(--text-main)",
    r"rgba\(26,107,255,\.12\)": "var(--info-bg)",
    r"rgba\(26,107,255,\.16\)": "var(--info-bg)",
    r"rgba\(74,144,217,\.2\)": "var(--info-border)",
    r"#8fc0ff": "var(--info-main)",
}

new_content = content
for old, new in color_map.items():
    # We use re.sub with fixed word boundaries if it's a hex, or literal replace for rgba
    if old.startswith("#"):
        new_content = re.sub(re.escape(old) + r"(?![0-9a-fA-F])", new, new_content, flags=re.IGNORECASE)
    else:
        new_content = new_content.replace(old, new)

# Special cleanups for gradients
new_content = new_content.replace("#1455cc", "var(--ant-primary-700)")
new_content = new_content.replace("#00816a", "var(--ant-success)")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Color refactor complete.")
