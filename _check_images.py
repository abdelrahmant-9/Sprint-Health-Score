import base64
import os

folder = r"C:\Users\ascom11\AppData\Local\Temp\antigravity\brain\e5a83de3-1754-4a41-8756-a9267f8c7b06"
# Wait, the dir command showed C:\Users\ascom11\.gemini\antigravity\brain\e5a83de3-1754-4a41-8756-a9267f8c7b06
folder = r"C:\Users\ascom11\.gemini\antigravity\brain\e5a83de3-1754-4a41-8756-a9267f8c7b06"

img1 = os.path.join(folder, "media__1775671590458.png")
img2 = os.path.join(folder, "media__1775671600327.png")

with open(img1, "rb") as f:
    b64_1 = base64.b64encode(f.read()).decode("utf-8")
    print(f"IMG1_B64_START: {b64_1[:50]}...{b64_1[-50:]}")
    print(f"IMG1_SIZE: {len(b64_1)}")

with open(img2, "rb") as f:
    b64_2 = base64.b64encode(f.read()).decode("utf-8")
    print(f"IMG2_B64_START: {b64_2[:50]}...{b64_2[-50:]}")
    print(f"IMG2_SIZE: {len(b64_2)}")
