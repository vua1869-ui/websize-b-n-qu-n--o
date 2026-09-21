# AURA STUDIO — Cửa hàng thời trang tích hợp AI

Web bán quần áo (FastAPI + HTML/JS) với: **AI Stylist chat**, **AI tính size**, **AI phối đồ (combo giảm 15%)**,
**Flash Sale đếm ngược theo giờ server**, voucher, giỏ hàng, thanh toán COD/chuyển khoản, phòng Live AI, lookbook video.

## Chạy nhanh

Yêu cầu: **Python 3.10+**.

```bash
# 1. (khuyến nghị) tạo môi trường ảo
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. cài thư viện
pip install -r requirements.txt

# 3. chạy
python run.py
```

Mở trình duyệt: **http://127.0.0.1:8000**  (tài liệu API tự sinh: http://127.0.0.1:8000/docs)

Không cần cài Node, không cần API key — AI mặc định dùng bộ luật nội bộ nên web chạy được ngay.

## Cấu hình (tùy chọn)

Sao chép `.env.example` thành `.env` rồi chỉnh. Các biến chính:

| Biến | Ý nghĩa |
|---|---|
| `AI_ENGINE` | `auto` (Gemini → Ollama → luật nội bộ), `gemini`, `ollama`, `rules` |
| `OLLAMA_HOST`, `OLLAMA_MODEL` | Dùng LLM chạy local qua [Ollama](https://ollama.com) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Dùng Google Gemini (key lấy tại aistudio.google.com/apikey) |
| `SECRET_KEY` | Khóa ký ưu đãi combo. **Đổi khi triển khai thật.** |
| `SHIPPING_FEE`, `FREE_SHIPPING_THRESHOLD`, `COMBO_DISCOUNT_PERCENT` | Phí ship, ngưỡng freeship, % giảm combo |

Nếu Gemini/Ollama lỗi hoặc tắt, chat tự chuyển sang bộ luật nội bộ (không treo, không báo lỗi cho khách).

## Thêm sản phẩm

Sản phẩm nằm trong `app/data/products.json` (đọc một lần khi khởi động, sửa xong nhớ chạy lại `python run.py`).
Copy một khối có sẵn, đặt `id` mới liên tiếp (`prod_061`, ...) rồi sửa. Lưu ý:

- `category` là một trong: `ao_thun`, `ao_so_mi`, `ao_khoac`, `quan_tay`, `quan_jeans`, `chan_vay`, `vay_dam`, `set_do`, `phu_kien`.
- `sizes`: size chữ (`S`…`XXL`), size số cho quần jeans (`"28"`, `"30"`) hoặc một mục bắt đầu bằng `Free`. AI tính size dựa vào đây.
- `occasions` nên dùng mã có sẵn (`cong_so`, `di_bien`, `du_tiec`, `hen_ho`, `thu_dong`...). Chat AI và phối đồ dùng trường này để chọn món.
- `tags` viết không dấu, chữ thường. `flash_sale: true` cần thêm `flash_sale_price`.
- Ảnh: thay `images` bằng ảnh thật của bạn. Sản phẩm mẫu đang dùng lại ảnh Unsplash có sẵn.

Chat AI (bộ luật nội bộ) tự đưa sản phẩm mới vào các kịch bản (đi làm, dạ tiệc, đi biển, hẹn hò, thu đông,
streetwear) theo `occasions`/`tags`; bảng ánh xạ nằm ở `SCENARIO_OCCASIONS` trong `app/services/ai_service.py`.

## Kiểm thử

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

39 test bao phủ: giá do server tính, chống gian lận giá/số lượng/combo, tồn kho, voucher, tính size, phối đồ, chat (kể cả Ollama giả).

## Cấu trúc

```
app/
  main.py                 FastAPI: route, xử lý lỗi, giới hạn tần suất gọi AI
  config.py               Cấu hình (đọc .env)
  models/schemas.py       Schema + ràng buộc dữ liệu
  services/
    product_service.py    Lọc/tìm kiếm (không dấu), danh mục, voucher, kho, Flash Sale
    order_service.py      Tính giá, voucher, ship, combo (có chữ ký), lưu đơn
    outfit_service.py     Phối đồ + tính size theo từng sản phẩm
    ai_service.py         Gemini / Ollama / bộ luật nội bộ, bot Live
    text_utils.py         Bỏ dấu tiếng Việt, khớp nguyên từ
  data/products.json      Danh mục sản phẩm
  data/orders.jsonl       Đơn hàng (tự tạo khi có đơn đầu tiên)
  static/ , templates/    Giao diện
tests/                    Test tự động
```

## Chỉnh sửa giao diện

CSS tiện ích được build sẵn vào `app/static/css/tailwind.css` nên **chạy web không cần Node**.
Chỉ khi bạn sửa class Tailwind trong `templates/` hoặc `static/js/` mới cần build lại:

```bash
npm install
npm run build:css        # hoặc: npm run watch:css
```

## Những gì cần lưu ý trước khi bán thật

- **Thanh toán chuyển khoản chưa tích hợp cổng thanh toán**: đơn được ghi nhận ở trạng thái `pending_payment`, bạn cần tự đối soát. Muốn thu tiền tự động cần tích hợp VNPay/MoMo/PayOS...
- **Đơn hàng lưu ở file `orders.jsonl`**, chưa có trang quản trị, đăng nhập, hay gửi email/SMS. Lượng đơn lớn nên chuyển sang cơ sở dữ liệu (SQLite/PostgreSQL).
- **Kho theo sản phẩm**, chưa theo từng size/màu.
- **Ảnh/video là dữ liệu mẫu** (Unsplash). Ảnh lỗi sẽ tự hiện ảnh thay thế. Điền `video_url` trong `product_service.get_videos()` để phát video thật.
- Phần "Live AI" là mô phỏng (host AI trả lời bình luận), không phải livestream thật.
- Các chính sách hiển thị (đổi trả 15 ngày, cam kết chất lượng) là nội dung mẫu, hãy chỉnh cho đúng chính sách của bạn.
