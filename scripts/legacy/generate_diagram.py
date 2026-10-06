import zlib
import base64
import urllib.request
import os

mermaid_code = """sequenceDiagram
    participant User as Khách hàng
    participant FE as Frontend
    participant API as FastAPI Backend
    participant Geo as Geo Service
    participant DB as SQLite DB

    User->>FE: Điền thông tin giao hàng & Tỉnh/Xã
    FE->>API: POST /api/orders (Dữ liệu đơn hàng)
    API->>Geo: Validate Tỉnh/Thành & Xã/Phường
    Geo-->>API: Trả về kết quả Hợp lệ/Không hợp lệ
    alt Không hợp lệ
        API-->>FE: HTTP 422 (Lỗi địa chỉ)
    else Hợp lệ
        API->>DB: Trừ tồn kho & Lưu đơn hàng (orders, order_items)
        API-->>FE: HTTP 200 (Thành công + QR Code)
    end
"""

compressed = zlib.compress(mermaid_code.encode('utf-8'), 9)
encoded = base64.urlsafe_b64encode(compressed).decode('ascii')
url = f"https://kroki.io/mermaid/png/{encoded}"

req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    output_path = r"c:\Users\Duc Cuong\OneDrive\Desktop\ass tb\websize-b-n-qu-n--o\luong_dat_hang.png"
    with open(output_path, 'wb') as f:
        f.write(response.read())
print(f"Saved to {output_path}")
