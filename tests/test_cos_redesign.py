from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.services.product_service import product_service


def test_homepage_curated_products(client: TestClient):
    """Trang chủ chỉ hiển thị các khu vực chọn lọc, mỗi khu vực tối đa 8 sản phẩm và mở công khai không cần cookie."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    soup = BeautifulSoup(resp.text, "html.parser")

    # Kiểm tra tiêu đề triết lý COS
    assert "Tối giản trong từng đường nét" in resp.text
    assert "Xem tất cả sản phẩm" in resp.text

    # Kiểm tra khu vực Hot nhất tuần
    hot_section = soup.find("section", {"id": "featured-hot"})
    assert hot_section is not None
    hot_cards = hot_section.find_all("article")
    assert 0 < len(hot_cards) <= 8

    # Kiểm tra khu vực Mới về
    new_section = soup.find("section", {"id": "featured-new"})
    assert new_section is not None
    new_cards = new_section.find_all("article")
    assert 0 < len(new_cards) <= 8

    # Các card dẫn thẳng đến /product/{id}
    for card in hot_cards + new_cards:
        link = card.find("a", href=True)
        assert link is not None
        assert link["href"].startswith("/product/")


def test_products_catalog_filters(client: TestClient):
    """Trang /products hiển thị đầy đủ danh mục, hỗ trợ bộ lọc và không yêu cầu đăng nhập."""
    resp = client.get("/products")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    soup = BeautifulSoup(resp.text, "html.parser")

    # Có tiêu đề và danh sách sản phẩm
    assert "Tất cả sản phẩm" in resp.text
    grid = soup.find("div", {"id": "products-grid"})
    assert grid is not None
    cards = grid.find_all("article")
    assert len(cards) > 8  # Danh mục đầy đủ nhiều hơn 8 sản phẩm

    # Lọc theo danh mục
    resp_cat = client.get("/products?category=ao_thun")
    assert resp_cat.status_code == 200
    soup_cat = BeautifulSoup(resp_cat.text, "html.parser")
    cat_cards = soup_cat.find("div", {"id": "products-grid"}).find_all("article")
    assert len(cat_cards) > 0

    # Lọc theo giới tính
    resp_gender = client.get("/products?gender=nam")
    assert resp_gender.status_code == 200

    # Lọc theo từ khóa tìm kiếm
    resp_search = client.get("/products?search=linen")
    assert resp_search.status_code == 200


def test_product_detail_page(client: TestClient):
    """Trang chi tiết /product/{id} trả về đầy đủ thông tin, ảnh lớn, chọn size/màu, đánh giá và gợi ý liên quan."""
    p = product_service.get_by_id("prod_001")
    assert p is not None

    resp = client.get(f"/product/{p.id}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]

    # Kiểm tra các thông tin chính
    assert p.name in resp.text
    assert p.material in resp.text
    assert "Thêm vào giỏ hàng" in resp.text
    assert "Bảng chọn size" in resp.text
    assert "Đánh giá từ khách hàng" in resp.text

    soup = BeautifulSoup(resp.text, "html.parser")

    # Ảnh chính
    main_img = soup.find("img", {"id": "qv-main-img"})
    assert main_img is not None

    # Khung chọn size và màu
    assert soup.find("div", {"id": "qv-colors"}) is not None
    assert soup.find("div", {"id": "qv-sizes"}) is not None

    # Khung đánh giá
    assert soup.find("div", {"id": "qv-reviews-container"}) is not None

    # Gợi ý Có thể bạn thích (tối đa 4 sản phẩm)
    related_articles = soup.find_all("article")
    assert len(related_articles) <= 4


def test_product_detail_404_on_invalid_id(client: TestClient):
    """Khi truy cập /product/{id} với id không tồn tại, trả về status 404 và template 404 thân thiện."""
    resp = client.get("/product/non_existent_product_xyz_999")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "404" in resp.text
    assert "Không tìm thấy" in resp.text
    assert "Khám phá bộ sưu tập" in resp.text
    assert "Về trang chủ" in resp.text


def test_public_routes_accessible_without_session(client: TestClient):
    """Xác nhận các route công khai /, /products, /product/{id} hoạt động bình thường không cần session cookie."""
    client.cookies.clear()

    resp_home = client.get("/")
    assert resp_home.status_code == 200

    resp_products = client.get("/products")
    assert resp_products.status_code == 200

    resp_detail = client.get("/product/prod_001")
    assert resp_detail.status_code == 200

    resp_404 = client.get("/product/invalid_id_test")
    assert resp_404.status_code == 404


def test_vertical_sidebar_layout_storefront(client: TestClient):
    """Kiểm tra thanh điều hướng dọc (sidebar) và cấu trúc mobile drawer trên các trang khách hàng."""
    for path in ["/", "/products", "/product/prod_001"]:
        res = client.get(path)
        assert res.status_code == 200
        soup = BeautifulSoup(res.text, "html.parser")

        # Sidebar dọc cố định
        sidebar = soup.find("aside", {"id": "site-sidebar"})
        assert sidebar is not None, f"Missing #site-sidebar on {path}"
        assert "site-sidebar" in sidebar.get("class", [])

        # Ô tìm kiếm full-width dưới logo
        search_form = sidebar.find("form", {"id": "search-form"})
        assert search_form is not None, f"Missing #search-form inside sidebar on {path}"
        search_input = search_form.find("input", {"id": "search-input"})
        assert search_input is not None, f"Missing #search-input inside sidebar on {path}"

        # Các mục menu dọc: Trang chủ, Tất cả sản phẩm, Stylist AI, Yêu thích, Giỏ hàng
        assert sidebar.find("a", href="/") is not None
        assert sidebar.find("a", href="/products") is not None
        assert sidebar.find("button", {"data-action": "open-chat"}) is not None
        assert sidebar.find("button", {"data-action": "show-wishlist"}) is not None
        assert sidebar.find("button", {"data-action": "open-cart"}) is not None
        assert sidebar.find("span", class_="cart-badge") is not None

        # Container tài khoản/đăng nhập
        auth_container = sidebar.find("div", {"id": "header-auth-container"})
        assert auth_container is not None, f"Missing #header-auth-container on {path}"

        # Thanh mobile top bar & overlay drawer
        mobile_bar = soup.find("header", class_="mobile-top-bar")
        assert mobile_bar is not None, f"Missing .mobile-top-bar on {path}"
        assert mobile_bar.find("button", {"id": "mobile-menu-open-btn"}) is not None
        assert soup.find("div", {"id": "sidebar-overlay"}) is not None

        # Khung nội dung chính dịch sang phải
        main_layout = soup.find("div", class_="main-content-layout")
        assert main_layout is not None, f"Missing .main-content-layout on {path}"


def test_vertical_sidebar_layout_admin(client: TestClient):
    """Kiểm tra thanh điều hướng dọc (sidebar) và trạng thái active trên giao diện Admin."""
    # Đăng nhập Admin
    login_res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    cookies = {"aura_session": token}
    headers = {"Authorization": f"Bearer {token}"}

    # Kiểm tra Dashboard
    res_dash = client.get("/admin", cookies=cookies, headers=headers)
    assert res_dash.status_code == 200
    soup_dash = BeautifulSoup(res_dash.text, "html.parser")

    admin_sidebar = soup_dash.find("aside", {"id": "admin-sidebar"})
    assert admin_sidebar is not None
    assert "admin-sidebar" in admin_sidebar.get("class", [])

    # Kiểm tra các mục menu admin
    assert admin_sidebar.find("a", href="/admin") is not None
    assert admin_sidebar.find("a", href="/admin/products") is not None
    assert admin_sidebar.find("a", href="/admin/inventory") is not None
    assert admin_sidebar.find("a", href="/admin/orders") is not None
    assert admin_sidebar.find("a", href="/admin/trending") is not None
    assert admin_sidebar.find("a", href="/admin/users") is not None
    assert admin_sidebar.find("button", {"id": "admin-logout-btn"}) is not None

    # Nút mở drawer trên mobile và overlay
    assert soup_dash.find("button", {"id": "admin-mobile-menu-btn"}) is not None
    assert soup_dash.find("div", {"id": "admin-sidebar-overlay"}) is not None
    assert soup_dash.find("div", class_="admin-main-layout") is not None

    # Kiểm tra active class trên Dashboard menu item
    dash_link = admin_sidebar.find("nav").find("a", href="/admin")
    assert "active" in dash_link.get("class", [])

