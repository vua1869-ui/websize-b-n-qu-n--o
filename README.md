# AURA STUDIO — Cửa hàng thời trang tích hợp AI

Web bán quần áo (FastAPI + HTML/JS) với: **AI Stylist chat**, **AI tính size**, **AI phối đồ (combo giảm 15%)**,
**Flash Sale đếm ngược theo giờ server**, voucher, giỏ hàng, thanh toán COD/chuyển khoản, phòng Live AI, lookbook video.

## Chạy nhanh

Yêu cầu: **Python 3.10+**.

```bash
# 1. (khuyến nghị) tạo môi trường ảo
python -m venv .venv
.venv\\Scripts\\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. cài thư viện (bao gồm python-multipart cho upload file sản phẩm CSV/Excel)
pip install -r requirements.txt

# 3. (lần đầu tiên) chạy migration đưa dữ liệu demo vào database
python scripts/migrate_to_db.py

# 4. chạy
python run.py
```

Mở trình duyệt: **http://127.0.0.1:8000**  (tài liệu API tự sinh: http://127.0.0.1:8000/docs)

Không cần cài Node, không cần API key — AI mặc định dùng bộ luật nội bộ nên web chạy được ngay.

## Bảo mật & Cấu hình (quan trọng)

> **⚠️ CẢNH BÁO:** Nếu bạn đã clone repo này và thấy rằng `CLOUDINARY_API_SECRET` hoặc `VNPAY_HASH_SECRET` đã từng có giá trị thật trong lịch sử Git, **hãy vào dashboard Cloudinary (cloudinary.com/console → Settings → Access Keys → Regenerate) và VNPay Merchant Portal để thu hồi và tạo key mới ngay**. Key cũ có thể đã bị lộ công khai.

Sao chép `.env.example` thành `.env` rồi chỉnh. Các biến chính:

| Biến | Ý nghĩa |
|---|---|
| `AI_ENGINE` | `auto` (Gemini → Ollama → luật nội bộ), `gemini`, `ollama`, `rules` |
| `OLLAMA_HOST`, `OLLAMA_MODEL` | Dùng LLM chạy local qua [Ollama](https://ollama.com) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Dùng Google Gemini (key lấy tại aistudio.google.com/apikey) |
| `SECRET_KEY` | Khóa ký ưu đãi combo. **Đổi khi triển khai thật.** |
| `VNPAY_TMN_CODE`, `VNPAY_HASH_SECRET` | Thông tin cổng thanh toán VNPay (lấy tại VNPay Merchant Portal) |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | Lưu ảnh (lấy tại cloudinary.com/console) |
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

## Kiểm thử & Chất lượng mã nguồn

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
python -m ruff check app
```

Hơn **182 bài test tự động** (100% pass) bao phủ:
- **Kiến trúc & Router**: 9 module APIRouter độc lập (`pages`, `products`, `ai`, `orders`, `payment`, `auth`, `cart`, `admin`, `loyalty`).
- **Bảo mật & Phòng chống hồi quy**: Chống IDOR kiểm tra đơn hàng, timing attack login, IP rate limit đăng ký/đăng nhập, lọc XSS server-side, bảo vệ Host header reset password, session refresh khi đổi mật khẩu, itsdangerous session serializer.
- **Tính trung thực dữ liệu**: Đánh giá dựa trên đơn hàng đã giao (verified buyer), chống review ảo, script xóa demo data, phân bổ doanh thu ròng và giá vốn bình quân gia quyền (WAC) trong báo cáo P&L.
- **Tối ưu AI Stylist**: Giới hạn ngữ cảnh 30 sản phẩm dựa trên tìm kiếm ngữ nghĩa, giới hạn concurrency semaphore fallback sang bộ luật nội bộ.
- **AI Fashion Trend Detection**: Chuẩn hóa Google Trends bằng anchor keyword, điểm xu hướng, cache TTL và matching tồn kho.

## Cấu trúc mã nguồn

```
app/
  main.py                 FastAPI lifespan, middleware bảo mật, exception handlers & mount routers
  config.py               Cấu hình Pydantic Settings đọc .env
  core/
    logging.py            Hệ thống log tập trung, che giấu SĐT/PII, log sự kiện tài chính
  routers/
    pages.py              Server-rendered HTML pages & SEO metadata
    products.py           Chi tiết sản phẩm, danh mục, voucher, reviews
    ai.py                 AI Stylist Chatbot, AI tính size, phòng Live AI
    orders.py             Báo giá, tạo đơn, tra cứu vận đơn trung thực
    payment.py            Cổng VNPay sandbox & webhook ngân hàng
    auth.py               Đăng ký, đăng nhập an toàn, đổi mật khẩu, quên mật khẩu
    cart.py               Giỏ hàng cookie & đồng bộ giỏ
    admin.py              Bảng điều khiển quản trị, quản lý đơn, nhập xuất kho, báo cáo P&L
    loyalty.py            Hạng thẻ & tích điểm thành viên (Bạc/Vàng/Kim Cương)
  models/schemas.py       Pydantic v2 schemas với validation chặt chẽ
  db/                     SQLAlchemy ORM models, session & atomic database service
  services/               Logic nghiệp vụ (sản phẩm, đơn hàng, AI, trend, email, ảnh...)
scripts/
  clear_demo_social_proof.py  Script xóa dữ liệu ảo (hỗ trợ --dry-run)
  migrate_to_db.py            Khởi tạo database và dữ liệu
tests/                    182 kịch bản kiểm thử toàn diện
```

## Chỉnh sửa giao diện

CSS tiện ích được build sẵn vào `app/static/css/tailwind.css` nên **chạy web không cần Node**.
Chỉ khi bạn sửa class Tailwind trong `templates/` hoặc `static/js/` mới cần build lại:

```bash
npm install
npm run build:css        # hoặc: npm run watch:css
```

## Triển khai Docker & Production

Ứng dụng hỗ trợ đóng gói Docker với cấu hình chuẩn bảo mật không chạy quyền root:

```bash
# Build Docker image
docker build -t aura-studio .

# Chạy container (khuyến nghị chạy 1 worker uvicorn khi dùng CSDL SQLite cục bộ)
docker run -d -p 8000:8000 --env-file .env aura-studio
```

Khi chạy trực tiếp qua `uvicorn`, nên sử dụng:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

- [ ] **Đặt `APP_ENV=production` & `DEBUG=false`** trong file `.env`.
- [ ] **Đổi `SECRET_KEY`**: Tạo chuỗi ngẫu nhiên bằng `python -c "import secrets; print(secrets.token_hex(32))"`. Không dùng chuỗi dev mặc định.
- [ ] **Đổi `PAYMENT_WEBHOOK_SECRET`**: Khóa bí mật khớp với cấu hình webhook thanh toán của VNPay/Ngân hàng.
- [ ] **Cấu hình CSDL PostgreSQL**: Thay `DATABASE_URL` bằng kết nối PostgreSQL/MySQL thực tế (nếu triển khai nhiều replica).
- [ ] **Cập nhật `PUBLIC_BASE_URL` & `VNPAY_RETURN_URL`**: Trỏ về URL tên miền chính thức có `https://...` (không dùng `127.0.0.1` hay `localhost`).
- [ ] **Cấu hình Email Sender**: Đặt `EMAIL_BACKEND` thành `smtp` hoặc `resend`, cung cấp đầy đủ thông số gửi mail cho tính năng quên mật khẩu.
- [ ] **Đổi mật khẩu tài khoản Admin mặc định**: Đăng nhập tài khoản `admin` ban đầu và tiến hành đổi mật khẩu mới ngay lập tức.
- [ ] **Tắt Swagger Docs**: Khi `APP_ENV=production`, hệ thống tự động tắt `/docs`, `/redoc` và `/openapi.json` để bảo mật.

## Những gì cần lưu ý khi vận hành thật

- **Dữ liệu đánh giá & minh chứng xã hội**: Thiết lập `DEMO_DATA=false` trong `.env` để không hiển thị điểm đánh giá giả định khi sản phẩm chưa có review thật. Chạy `python scripts/clear_demo_social_proof.py` để xóa sạch các lượt mua và đánh giá ảo demo.
- **Vận chuyển & GHN**: Hệ thống trả về trạng thái chuẩn "Chờ bàn giao đối tác vận chuyển" và "Chưa có mã vận đơn" khi đơn chưa được bàn giao đơn vị vận chuyển thực tế, không bịa mã vận đơn giả.
- **Giá vốn & Báo cáo Lợi nhuận (P&L)**: Khi nhập hàng trong trang Admin, điền giá vốn nhập kho (`cost_price`). Hệ thống tính toán chính xác Giá vốn bình quân gia quyền (Weighted Average Cost - WAC) và phân bổ doanh thu ròng sau voucher để xuất báo cáo lãi/lỗ chi tiết.
- **Đảo ngược & Xóa sản phẩm**: Xóa sản phẩm qua trang quản trị sử dụng cơ chế Soft-delete (`is_active = False`, `deleted_at = datetime.utcnow`), bảo toàn toàn vẹn lịch sử đơn hàng, đánh giá và các lô hàng nhập kho trước đó.

## Địa giới hành chính 2 cấp (Áp dụng từ 1/7/2025)

Theo **Nghị quyết 202/2025/QH15** của Quốc hội thông qua ngày 12/6/2025, từ ngày **1/7/2025** Việt Nam chính thức bãi bỏ cấp trung gian Quận/Huyện, cả nước tổ chức lại thành **34 Tỉnh/Thành phố** với mô hình hành chính 2 cấp:
- **Cấp 1:** Tỉnh / Thành phố trực thuộc Trung ương (34 đơn vị).
- **Cấp 2:** Xã / Phường / Đặc khu trực thuộc Tỉnh/Thành phố.

### 1. Nguồn dữ liệu & Cơ chế hoạt động
- Dữ liệu chuẩn được trích xuất từ thư viện chính thức [`vietnam-provinces`](https://github.com/sunshine-tech/VietnamProvinces) (dữ liệu Tổng cục Thống kê sau đợt sáp nhập hành chính).
- File dữ liệu tĩnh lưu tại `app/data/vn_locations.json`, được nạp và cache trực tiếp vào bộ nhớ server khi khởi động (không phụ thuộc kết nối mạng lúc runtime).
- API endpoint: `GET /api/locations` cung cấp toàn bộ danh mục cho dropdown phía frontend.
- Quy trình thanh toán tự động xác thực nghiêm ngặt phía server: `ward_code` bắt buộc phải thuộc đúng `province_code` đã chọn (trả lỗi HTTP 422 rõ ràng nếu không khớp), lưu địa chỉ đầy đủ dạng chuỗi hiển thị chuẩn vào đơn hàng.

### 2. Cách cập nhật / Xuất lại dữ liệu
Nếu trong tương lai Nhà nước có đợt điều chỉnh, sáp nhập hoặc chia tách địa giới hành chính mới:
```bash
# 1. Cập nhật thư viện dữ liệu lên phiên bản mới nhất
pip install --upgrade vietnam-provinces

# 2. Chạy script xuất dữ liệu ra file tĩnh
python scripts/export_locations.py
```
Script sẽ tự động đồng bộ lại toàn bộ mã code, tên gọi chuẩn của Tỉnh/Thành phố và Xã/Phường vào file `app/data/vn_locations.json`.