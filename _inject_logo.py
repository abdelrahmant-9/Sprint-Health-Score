import base64
import os

# Path to the image provided by the user in the project directory
img_path = r"d:\Sprint Health Script\Icon.png"
script_path = r"d:\Sprint Health Script\sprint_health_2.py"

if not os.path.exists(img_path):
    print(f"Error: Image not found at {img_path}")
    exit(1)

with open(img_path, "rb") as f:
    b64_data = base64.b64encode(f.read()).decode("utf-8")

with open(script_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace the broken/truncated placeholder with the full base64
# We look for the src="data:image/png;base64,... part
import re
new_content = re.sub(
    r'src="data:image/png;base64,[^"]+"',
    f'src="data:image/png;base64,{b64_data}"',
    content
)

with open(script_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Successfully injected full base64 logo into script.")
