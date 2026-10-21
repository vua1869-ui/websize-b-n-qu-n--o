# HƯỚNG DẪN TRIỂN KHAI VÀ BẢO VỆ ASSIGNMENT WEB1014 (GIAI ĐOẠN 2)
### Đề tài: Website Thời Trang AURA FASHION
---

## PHẦN 1: CẤU TRÚC THƯ MỤC DỰ ÁN
Toàn bộ mã nguồn của Giai đoạn 2 nằm trọn vẹn trong thư mục `assignment-web1014/`, gồm có:
* `index.html`: Trang chủ (Header, Topbar, Banner Hero, Cam kết chính sách, Danh mục, Lưới sản phẩm, Video Lookbook & Audio bản tin, Footer).
* `san-pham.html`: Trang cửa hàng (Cột lọc đa năng checkbox/radio, Bộ chọn sắp xếp, Lưới CSS Grid sản phẩm, Phân trang).
* `chi-tiet.html`: Trang chi tiết sản phẩm (Bộ ảnh gallery đổi thumbnail, Form chọn size/màu/số lượng, **Bảng HTML Table thông số kích cỡ Size Chart**).
* `gio-hang.html`: Trang giỏ hàng & thanh toán (**Bảng HTML Table danh sách sản phẩm giỏ hàng**, **Form HTML thanh toán** với đầy đủ các loại input text, tel, email, select, textarea, radio, checkbox).
* `gioi-thieu.html`: Trang giới thiệu (Câu chuyện thương hiệu, Đội ngũ sáng lập, Video quy trình may đo thủ công).
* `lien-he.html`: Trang liên hệ (**Form góp ý**, **Bảng HTML Table hệ thống showroom & giờ mở cửa**, Bản đồ Google Maps nhúng iframe).
* `dang-nhap.html` & `dang-ky.html`: Trang xác thực thành viên với form chuẩn mực.
* `css/style.css`: File định kiểu CSS thuần (Áp dụng Box Model, Flexbox, CSS Grid, CSS Variables, Responsive `@media`).
* `js/main.js`: Tương tác bật/tắt Mobile Menu, đổi ảnh Thumbnail, tính giỏ hàng, thông báo đặt hàng.

---

## PHẦN 2: ĐỐI CHIẾU TIÊU CHÍ CHẤM ĐIỂM (N2 – N4)

### 1. Menu điều hướng, header, footer, bố cục rõ ràng
* **Header & Topbar:** Có thanh thông báo freeship, logo thương hiệu, menu điều hướng đa trang và các icon giỏ hàng/tài khoản.
* **Footer:** 4 cột cân đối chứa đầy đủ: Giới thiệu thương hiệu, Liên kết nhanh, Chính sách đổi trả, Form đăng ký nhận tin bản tin.
* **Menu Mobile:** Nút Hamburger tự động mở menu dạng trượt khi xem trên điện thoại.

### 2. Sử dụng Box model, Flexbox hoặc Grid layout
* **Box Model:** Thiết lập `box-sizing: border-box` toàn trang; tính toán chuẩn margin, padding, border theo thang khoảng cách 4px-8px-16px-24px-32px.
* **Flexbox (`display: flex`):** Căn giữa và dãn đều Navbar (`justify-content: space-between; align-items: center`), căn chỉnh thẻ sản phẩm theo chiều dọc (`flex-direction: column; justify-content: space-between`), sắp xếp nút chọn size, số lượng.
* **Grid Layout (`display: grid`):** 
  * Lưới danh mục: `grid-template-columns: repeat(auto-fit, minmax(260px, 1fr))`
  * Lưới sản phẩm: `grid-template-columns: repeat(4, 1fr)` trên máy tính, tự động giảm về 2 cột trên điện thoại.
  * Trang cửa hàng: Bố cục 2 cột (Cột lọc 260px và Lưới sản phẩm 1fr).

### 3. Dùng bảng, form, hình ảnh, video/audio
* **Bảng (Table):** 
  * Bảng giỏ hàng (`gio-hang.html`): Sử dụng `<table>`, `<thead>`, `<tbody>`, `<tfoot>`, các ô `th`, `td` căn lề giá tiền và tổng cộng chuẩn mực.
  * Bảng Size Chart (`chi-tiet.html`): Bảng đối chiếu chiều cao, cân nặng, vòng ngực, rộng vai.
  * Bảng Showroom (`lien-he.html`): Liệt kê địa chỉ và giờ mở cửa từng chi nhánh.
* **Form:** Sử dụng đa dạng các thẻ `<input type="text|email|tel|number|radio|checkbox|date|password">`, `<select>`, `<textarea>`, `<button>` có kiểm tra dữ liệu `required` và pattern regex.
* **Hình ảnh & Video/Audio:** Hình ảnh thời trang độ phân giải cao; thẻ `<video controls>` Lookbook và thẻ `<audio controls>` bản tin âm nhạc showroom.

---

## PHẦN 3: HƯỚNG DẪN TRIỂN KHAI LÊN HOST MIỄN PHÍ

### CÁCH 1: Triển khai lên GITHUB PAGES (Khuyên dùng - Rất nhanh & Ổn định)
1. **Tạo Repository mới trên GitHub:**
   * Truy cập [https://github.com](https://github.com) -> Đăng nhập -> Chọn **New repository**.
   * Đặt tên kho lưu trữ: `assignment-web1014` -> Chọn chế độ **Public** -> Bấm **Create repository**.
2. **Đẩy thư mục `assignment-web1014` lên GitHub:**
   * Mở thư mục `assignment-web1014` trên máy tính của bạn.
   * Kéo thả toàn bộ các file (`index.html`, `san-pham.html`, `css/`, `js/`...) vào trang web GitHub (mục "uploading an existing file") hoặc dùng lệnh git:
     ```bash
     cd assignment-web1014
     git init
     git add .
     git commit -m "Upload Assignment WEB1014 Phase 2"
     git branch -M main
     git remote add origin https://github.com/<tai-khoan-cua-ban>/assignment-web1014.git
     git push -u origin main
     ```
3. **Bật GitHub Pages:**
   * Trong repository trên GitHub, vào tab **Settings** -> Tìm mục **Pages** (cột bên trái).
   * Tại phần **Build and deployment** -> **Branch**, chọn nhánh `main` và thư mục `/(root)` -> Bấm **Save**.
4. **Lấy link nộp bài:**
   * Sau khoảng 1-2 phút, GitHub sẽ hiển thị đường link trang web của bạn:
   * `https://<ten-tai-khoan-github>.github.io/assignment-web1014/`
   * Bạn chỉ cần copy link này dán vào bài nộp cho giảng viên.

---

### CÁCH 2: Triển khai lên 000WEBHOST
1. Đăng ký tài khoản miễn phí tại [https://www.000webhost.com](https://www.000webhost.com).
2. Tạo một trang web mới (đặt tên miền phụ miễn phí, ví dụ: `aurafashion-web1014.000webhostapp.com`).
3. Truy cập vào **File Manager** (Quản lý tệp tin) -> Chọn thư mục **`public_html`**.
4. Nén toàn bộ nội dung bên trong thư mục `assignment-web1014` thành file `.zip` rồi upload lên `public_html` và giải nén (Extract) sao cho file `index.html` nằm trực tiếp ngay trong `public_html`.
5. Truy cập đường link `https://aurafashion-web1014.000webhostapp.com` để kiểm tra.

---

### CÁCH 3: TRỎ TÊN MIỀN RIÊNG (NẾU CÓ)
Nếu bạn có một tên miền riêng (ví dụ từ Cloudflare, Tenten, Matbao):
1. **Trên GitHub Pages:**
   * Vào mục **Custom domain** trong trang cấu hình Pages của repository, nhập tên miền (ví dụ: `fashion.cuongdev.click`) -> Bấm **Save**.
2. **Tại trang quản trị DNS của nhà cung cấp tên miền:**
   * Thêm bản ghi **CNAME**:
     * **Host (Tên):** `fashion` (hoặc `@` nếu là domain gốc)
     * **Points to (Giá trị):** `<ten-tai-khoan-github>.github.io`
     * **TTL:** Automatic hoặc 3600
3. Tích chọn **Enforce HTTPS** trên GitHub để website được cấp chứng chỉ bảo mật SSL miễn phí.

---

## PHẦN 4: HƯỚNG DẪN NỘP FILE CHO GIẢNG VIÊN
* Để nộp file bài làm lên hệ thống học tập (LMS / AP):
  1. Chuột phải vào thư mục `assignment-web1014`.
  2. Chọn **Send to** -> **Compressed (zipped) folder** (hoặc dùng WinRAR chọn *Add to "assignment-web1014.zip"*).
  3. Đính kèm file `assignment-web1014.zip` và kèm theo đường link GitHub Pages đã triển khai ở trên vào phần mô tả nộp bài.
