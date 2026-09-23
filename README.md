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

## Tính năng mới: AI Fashion Trend Detection & Recommendation (Phát hiện xu hướng thời trang)

Hệ thống tự động phát hiện các xu hướng thời trang đang tăng trưởng quan tâm tại thị trường Việt Nam, chấm điểm xu hướng, đối soát thông minh với kho hàng `products.json`, và hiển thị nổi bật tại mục **🔥 TRENDING HÔM NAY** trên trang chủ.

### 1. Luồng hoạt động (Architecture Flow)
```text
Nguồn dữ liệu (Google Trends VN / Cache / Demo)
        ↓
Trend Collector & Keyword Normalization (bỏ dấu tiếng Việt, từ đồng nghĩa)
        ↓
Trend Analyzer (tính Growth Rate % & phân loại: rising, stable, declining)
        ↓
Trend Score (60% Tăng trưởng + 25% Tìm kiếm + 15% Sức bán tại shop)
        ↓
Product Matching (50% Từ khóa + 20% Danh mục + 15% Tag + 10% Style + 5% Đánh giá)
        ↓
Lọc hàng tồn kho (BẮT BUỘC: stock > 0, loại bỏ hoàn toàn đồ hết hàng)
        ↓
Xếp hạng gợi ý (Final Score = 70% Trend Score + 30% Product Match Score)
        ↓
Cá nhân hóa ẩn danh (Personalized = 70% Global Trend + 30% Sở thích người dùng)
        ↓
Giao diện AURA Studio ("🔥 TRENDING HÔM NAY" + Tích hợp AI Stylist + Tìm kiếm)
```

### 2. Nguồn dữ liệu & Cơ chế Fallback đa tầng
- **Google Trends thật** (qua `pytrends`): Lấy dữ liệu tìm kiếm tại Việt Nam (`geo='VN'`, múi giờ `Asia/Ho_Chi_Minh`, `timeframe='today 1-m'`).
- **Cache dữ liệu** (`app/data/trends.json`): Lưu kết quả và lịch sử biến động trend theo thời gian, TTL mặc định 6 giờ kèm khóa đồng bộ `threading.Lock` chống nghẽn khi nhiều người truy cập cùng lúc.
- **Demo / Fallback**: Nếu mạng gián đoạn hoặc API ngoài bị lỗi, hệ thống tự động chuyển sang dữ liệu chuẩn mực được chọn lọc sẵn mà không làm sập ứng dụng.
- **Minh bạch xuất xứ**: Giao diện và API luôn hiển thị rõ ràng nhãn nguồn: `google_trends`, `cached`, hoặc `demo`.

### 3. Các API Endpoints
| Phương thức | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/api/trends?limit=10` | Danh sách xu hướng đang tăng trưởng |
| `GET` | `/api/trending-products?limit=8&gender=...&category=...` | Danh sách sản phẩm bắt trend được đề xuất |
| `POST` | `/api/trends/refresh?force=true` | Làm mới dữ liệu, tính điểm và cập nhật cache |
| `GET` | `/api/trends/debug` | Thông tin chẩn đoán kỹ thuật (tuổi cache, nguồn dữ liệu) |

### 4. Cấu hình Trend trong `.env`
```env
TREND_ENABLED=true
TREND_COUNTRY=VN
TREND_CACHE_TTL=21600
TREND_LIMIT=10
TREND_PRODUCT_LIMIT=8
TREND_SOURCE=auto               # auto | google_trends | demo
TREND_REFRESH_TIMEOUT=12.0
TREND_RISING_THRESHOLD=20.0     # > 20% -> rising
TREND_DECLINING_THRESHOLD=-20.0 # < -20% -> declining
```

### 5. Cách bổ sung từ khóa xu hướng
Mở file `app/services/trend_service.py`:
- Thêm từ khóa vào danh sách `SEED_KEYWORDS`.
- (Tùy chọn) Thêm ánh xạ từ đồng nghĩa, phong cách và danh mục vào từ điển `TREND_TAXONOMY`.

## Kiểm thử

```bash
pip install -r requirements.txt
python -m pytest tests -q
```

Hơn 54 bài test tự động bao phủ:
- Toàn bộ tính năng bán hàng cốt lõi (giá server tính, chống gian lận, tồn kho, voucher, tính size, phối đồ, chat AI, đơn hàng).
- **12 bài test chuyên sâu cho AI Fashion Trend Detection**: chuẩn hóa điểm 0-100, nhận diện xu hướng tăng/giảm, matching sản phẩm chính xác, lọc hàng hết kho (`stock <= 0`), fallback khi nguồn lỗi, cache TTL, cá nhân hóa, API endpoints và tích hợp AI Stylist.

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
