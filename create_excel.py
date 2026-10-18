import pandas as pd

data = [
    ["CHỨC NĂNG", "", "", "", "", ""],
    ["", "", "", "", "", ""],
    ["- Công nghệ sử dụng:", "Front-end:\nHTML, CSS, Tailwind CSS, Javascript", "Back-end:\nPython (FastAPI)", "Database:\nJSON Files (SQLite/PostgreSQL)", "AI Integration:\nGoogle Gemini, Ollama, Google Trends", ""],
    ["", "", "", "", "", ""],
    ["Phân quyền", "Module", "Chức năng", "Mô tả chi tiết", "Ghi chú"],
    ["Admin", "Tổng quan (Dashboard)", "Thống kê", "Báo cáo doanh thu, số lượng đơn hàng, sản phẩm bán chạy, sản phẩm bắt trend (Trending)"],
    ["Admin", "Quản lý sản phẩm", "CRUD Sản phẩm", "Thêm/sửa/xóa thông tin sản phẩm. Quản lý kho, giá, % giảm giá, kích thước, tag sự kiện (Flash Sale), dịp sử dụng (đi làm, đi biển...)"],
    ["Admin", "Quản lý đơn hàng", "Xử lý đơn hàng", "Xem danh sách đơn, cập nhật trạng thái giao hàng, kiểm tra tính hợp lệ thanh toán (COD, VietQR, VNPay). Áp dụng giảm giá Combo AI."],
    ["Admin", "Quản lý khách hàng", "Danh sách người dùng", "Xem thông tin người dùng đăng ký, phân quyền Admin/Khách hàng."],
    ["Admin", "Quản lý xu hướng (AI Trend)", "Cập nhật Trends", "Quản trị bộ đệm xu hướng (Cache TTL), ép buộc làm mới dữ liệu từ Google Trends, cấu hình ngưỡng nhận diện xu hướng."],
    ["Khách hàng", "Xác thực tài khoản", "Đăng ký/Đăng nhập", "Đăng nhập/Đăng ký tài khoản, xem hồ sơ, theo dõi đơn hàng của tài khoản cá nhân."],
    ["Khách hàng", "Trang chủ & Duyệt sản phẩm", "Xem danh mục & Trending", "Trang chủ hiển thị danh mục, Flash Sale đếm ngược, và mục 🔥 TRENDING HÔM NAY (AI tự bắt trend và đối soát kho hàng để hiển thị)."],
    ["Khách hàng", "Danh sách sản phẩm", "Lọc & Tìm kiếm", "Tìm kiếm không dấu. Lọc theo danh mục, giới tính, khoảng giá. Sắp xếp theo phổ biến, bán chạy, giá tăng/giảm."],
    ["Khách hàng", "Tính năng AI: Stylist Chat", "Chat tư vấn", "Chatbot AI tư vấn mặc gì đi tiệc, đi chơi... Tự động Fallback giữa Gemini -> Ollama -> Bộ luật nội bộ nếu API lỗi."],
    ["Khách hàng", "Tính năng AI: Tính Size", "Gợi ý size", "Khách hàng nhập chiều cao, cân nặng, AI sẽ tính toán size chuẩn (S, M, L, XL, 30, 32...) cho từng món đồ cụ thể."],
    ["Khách hàng", "Tính năng AI: Phối Đồ", "Combo giảm giá", "AI gợi ý phối các sản phẩm với nhau thành 1 set. Nếu mua cả set sẽ được nhận chiết khấu tự động (VD: Giảm 15%)."],
    ["Khách hàng", "Tính năng AI: Live Room", "Phòng Live AI", "Mô phỏng Livestream. Khách hàng bình luận, Host AI tự động đọc và trả lời trực tiếp về sản phẩm."],
    ["Khách hàng", "Giỏ hàng", "Quản lý giỏ hàng", "Thêm/sửa/xóa sản phẩm, tính tổng tiền, tự động tính ngưỡng miễn phí vận chuyển (Freeship) và hiển thị thanh tiến độ."],
    ["Khách hàng", "Thanh toán (Checkout)", "Xử lý đặt hàng", "Điền thông tin giao hàng với địa giới hành chính mới (2 cấp: Tỉnh/Thành phố, Xã/Phường). Hỗ trợ mã Voucher."],
    ["Khách hàng", "Thanh toán (Checkout)", "Cổng thanh toán", "Lựa chọn thanh toán: Thanh toán khi nhận hàng (COD), Chuyển khoản tức thì (VietQR), hoặc Cổng VNPay."],
    ["Khách hàng", "Hậu mãi & Tra cứu", "Tra cứu đơn hàng", "Khách không cần đăng nhập vẫn có thể nhập SĐT hoặc Mã đơn để tra cứu hành trình vận chuyển."],
    ["Khách hàng", "Hậu mãi & Tra cứu", "Hóa đơn điện tử", "Hệ thống tự động sinh hóa đơn điện tử cho đơn hàng thành công, có thể in ra trực tiếp."]
]

df = pd.DataFrame(data)
df.to_excel('Quản_Lý_DATN_Aura_Studio.xlsx', index=False, header=False)
print('Excel file created successfully.')
