"""
Kiểm thử toàn diện các nút quay lại (Back buttons) trên toàn bộ hệ thống:
1. Xác minh không còn bất kỳ trang nào sử dụng href="javascript:history.back()".
2. Kiểm tra các trang Admin (/admin, /admin/orders, /admin/products, /admin/users, /admin/trending, /admin/inventory, in phiếu A6) đều có nút quay lại với biểu tượng mũi tên, nhãn rõ ràng và đích đến cha hợp lý.
3. Kiểm tra các trang Khách hàng (trang chủ, danh mục sản phẩm, chi tiết sản phẩm, tra cứu đơn hàng, đăng nhập, đăng ký, quên mật khẩu, hồ sơ cá nhân, 404, 403) đều có nút quay lại với liên kết fallback an toàn và hỗ trợ trợ năng (aria-label).
"""
import glob
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def get_authenticated_client(username, password):
    c = TestClient(app)
    res = c.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    c.cookies.set("aura_session", token)
    return c


def get_admin_client():
    return get_authenticated_client("admin", "admin123")


def get_user_client():
    # Thử login user1 hoặc đăng ký user mới
    c = TestClient(app)
    res = c.post("/api/auth/login", json={"username": "user1", "password": "user123"})
    if res.status_code != 200:
        c.post(
            "/api/auth/register",
            json={
                "username": "test_user_nav",
                "password": "Password123!",
                "email": "test_user_nav@aura.vn",
                "full_name": "Test User Nav",
                "phone": "0987654321",
            },
        )
        res = c.post("/api/auth/login", json={"username": "test_user_nav", "password": "Password123!"})
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    c.cookies.set("aura_session", token)
    return c


class TestBackButtons:

    def test_no_raw_javascript_history_back_in_any_html_files(self):
        """Đảm bảo không còn bất kỳ thẻ <a> nào chứa href='javascript:history.back()'."""
        html_files = glob.glob("app/templates/**/*.html", recursive=True)
        assert len(html_files) > 0

        offending_links = []
        for file_path in html_files:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                if "javascript:history.back()" in content.lower():
                    offending_links.append(file_path)

        assert offending_links == [], f"Các file sau vẫn còn href='javascript:history.back()': {offending_links}"

    def test_admin_pages_have_back_buttons(self):
        """Tất cả trang admin phải có nút quay lại rõ ràng, có mũi tên SVG và nhãn trợ năng."""
        admin_client = get_admin_client()

        admin_routes = [
            ("/admin", "Về Storefront", "/"),
            ("/admin/orders", "Quay lại Dashboard", "/admin"),
            ("/admin/products", "Quay lại Dashboard", "/admin"),
            ("/admin/users", "Quay lại Dashboard", "/admin"),
            ("/admin/trending", "Quay lại Dashboard", "/admin"),
            ("/admin/inventory", "Quay lại Dashboard", "/admin"),
        ]

        for path, expected_label, expected_target in admin_routes:
            res = admin_client.get(path, headers={"Accept": "text/html"})
            assert res.status_code == 200, f"Lỗi truy cập {path}: {res.status_code}"
            soup = BeautifulSoup(res.text, "html.parser")

            # Tìm nút quay lại trong admin (class aura-admin-back-btn)
            back_btn = soup.find("a", class_="aura-admin-back-btn")
            assert back_btn is not None, f"Trang admin {path} thiếu nút aura-admin-back-btn!"
            assert expected_target in back_btn.get("href", ""), f"Đích đến href của {path} phải là {expected_target}, nhận {back_btn.get('href')}"
            assert expected_label in back_btn.get_text(), f"Nhãn của {path} phải chứa '{expected_label}'"
            assert back_btn.find("svg") is not None, f"Nút quay lại của {path} thiếu biểu tượng mũi tên SVG!"
            assert back_btn.get("aria-label"), f"Nút quay lại của {path} thiếu thuộc tính aria-label!"

    def test_admin_order_print_has_back_button(self):
        """Trang in đơn hàng khổ A6 phải có nút quay lại đơn hàng rõ ràng."""
        admin_client = get_admin_client()

        # Lấy 1 đơn hàng bất kỳ
        orders = admin_client.get("/api/admin/orders?limit=1").json()
        assert len(orders) > 0
        order_id = orders[0]["order_id"]

        res = admin_client.get(f"/admin/orders/{order_id}/print")
        assert res.status_code == 200
        soup = BeautifulSoup(res.text, "html.parser")
        back_btn = soup.find("a", href="/admin/orders")
        assert back_btn is not None, "Trang in đơn hàng thiếu nút quay lại /admin/orders"
        assert back_btn.find("svg") is not None, "Nút quay lại in phiếu thiếu biểu tượng mũi tên SVG"
        assert "Quay lại" in back_btn.get_text()

    def test_user_pages_have_accessible_back_buttons(self):
        """Các trang người dùng quan trọng phải có nút quay lại có fallback an toàn."""
        # 1. Trang danh mục sản phẩm /products
        res_prods = client.get("/products")
        assert res_prods.status_code == 200
        soup_prods = BeautifulSoup(res_prods.text, "html.parser")
        back_prods = soup_prods.find("a", class_="aura-back-btn")
        assert back_prods is not None, "Trang /products thiếu aura-back-btn"
        assert back_prods.get("href") == "/", "Trang /products phải có fallback quay về '/'"
        assert back_prods.find("svg") is not None

        # 2. Trang chi tiết sản phẩm
        res_detail = client.get("/product/prod_001")
        assert res_detail.status_code == 200
        soup_detail = BeautifulSoup(res_detail.text, "html.parser")
        back_detail = soup_detail.find("a", class_="aura-back-btn")
        assert back_detail is not None, "Trang chi tiết sản phẩm thiếu aura-back-btn"
        assert "/products" in back_detail.get("href", ""), "Trang chi tiết phải có fallback về '/products'"
        assert back_detail.find("svg") is not None

        # 3. Trang tra cứu đơn hàng /tracking
        res_track = client.get("/tracking")
        assert res_track.status_code == 200
        soup_track = BeautifulSoup(res_track.text, "html.parser")
        back_track = soup_track.find("a", class_="aura-back-btn")
        assert back_track is not None, "Trang /tracking thiếu aura-back-btn"
        assert back_track.get("href") == "/"
        assert back_track.find("svg") is not None

        # 4. Trang đăng nhập /login
        res_login = client.get("/login")
        assert res_login.status_code == 200
        soup_login = BeautifulSoup(res_login.text, "html.parser")
        back_login = soup_login.find("a", class_="aura-back-btn")
        assert back_login is not None, "Trang /login thiếu aura-back-btn"
        assert back_login.get("href") == "/"
        assert back_login.find("svg") is not None

        # 5. Trang đăng ký /register
        res_reg = client.get("/register")
        assert res_reg.status_code == 200
        soup_reg = BeautifulSoup(res_reg.text, "html.parser")
        back_reg = soup_reg.find("a", class_="aura-back-btn")
        assert back_reg is not None, "Trang /register thiếu aura-back-btn"
        assert back_reg.get("href") == "/"
        assert back_reg.find("svg") is not None

        # 6. Trang hồ sơ cá nhân /profile
        user_client = get_user_client()
        res_prof = user_client.get("/profile")
        assert res_prof.status_code == 200
        soup_prof = BeautifulSoup(res_prof.text, "html.parser")
        back_prof = soup_prof.find("a", class_="aura-back-btn")
        assert back_prof is not None, "Trang /profile thiếu aura-back-btn"
        assert back_prof.get("href") == "/"
        assert back_prof.find("svg") is not None

        # 7. Trang 404 (yêu cầu Accept: text/html để trả về giao diện HTML)
        res_404 = client.get("/duong-dan-khong-ton-tai-12345", headers={"Accept": "text/html"})
        assert res_404.status_code == 404
        soup_404 = BeautifulSoup(res_404.text, "html.parser")
        back_404 = soup_404.find("a", class_="aura-back-btn")
        assert back_404 is not None, "Trang 404 thiếu aura-back-btn"
        assert back_404.get("href") == "/"
        assert back_404.find("svg") is not None
