import base64
import urllib.request

mermaid_code = """flowchart TD
    A[Nguồn dữ liệu: Google Trends / Cache / Demo] --> B[Trend Collector & Keyword Normalization]
    B --> C[Trend Analyzer: Tính Growth Rate % & Phân loại]
    C --> D[Trend Score: 60% Tăng trưởng + 25% Tìm kiếm + 15% Sức bán]
    D --> E[Product Matching: Từ khóa, Danh mục, Tag, Style]
    E --> F[Lọc hàng tồn kho: Bắt buộc Stock > 0]
    F --> G[Xếp hạng gợi ý: Final Score = 70% Trend + 30% Match]
    G --> H[Cá nhân hóa ẩn danh: 70% Global + 30% Sở thích]
    H --> I[Giao diện AURA Studio: TRENDING HÔM NAY]
"""

encoded = base64.b64encode(mermaid_code.encode('utf-8')).decode('ascii')
url = f"https://mermaid.ink/img/{encoded}"

req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        output_path = r"c:\Users\Duc Cuong\OneDrive\Desktop\ass tb\websize-b-n-qu-n--o\trend_detection.png"
        with open(output_path, 'wb') as f:
            f.write(response.read())
    print(f"Saved to {output_path}")
except Exception as e:
    print("Error:", e)
