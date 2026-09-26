import io
from unittest.mock import patch
import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.db.database import db_service
from app.main import app
from app.services.product_service import product_service

client = TestClient(app)


@pytest.fixture(scope="module")
def admin_headers():
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def user_headers():
    res = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ==========================================
# 1. KIỂM THỬ NHẬP HÀNG LOẠT (CSV / EXCEL)
# ==========================================

def test_admin_products_bulk_import_csv(admin_headers):
    """Import file CSV hợp lệ: sản phẩm được thêm vào kho thành công."""
    csv_content = (
        "name,category,gender,price,sizes,colors,occasions,tags,stock,images\n"
        "Áo Thun Graphic Test 1,ao_thun,unisex,199000,\"S, M, L\",\"Đen:#18181b, Trắng:#ffffff\",\"Dạo phố, Đi chơi\",\"cotton, graphic\",60,https://res.cloudinary.com/test/img1.jpg\n"
        "Quần Tây Dáng Suông Test 2,quan_tay,nam,399000,\"M, L, XL\",\"Đen, Xám\",\"Công sở, Đi làm\",\"quần tây, thanh lịch\",40,https://res.cloudinary.com/test/img2.jpg\n"
    ).encode("utf-8")

    files = {"file": ("products_test.csv", csv_content, "text/csv")}
    res = client.post("/api/admin/products/import", headers=admin_headers, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["total_rows"] == 2
    assert data["success_count"] == 2
    assert data["failed_count"] == 0
    assert len(data["imported_products"]) == 2

    # Kiểm tra sản phẩm đã có trong catalog
    names = [p.name for p in product_service.get_all()]
    assert "Áo Thun Graphic Test 1" in names
    assert "Quần Tây Dáng Suông Test 2" in names


def test_admin_products_bulk_import_validation_and_partial_failure(admin_headers):
    """File CSV có dòng lỗi và dòng hợp lệ: validate từng dòng, không fail cả file."""
    csv_content = (
        "name,category,gender,price,sizes,colors,occasions,tags,stock,images\n"
        "Sản phẩm Hợp lệ 1,ao_thun,unisex,250000,\"S, M\",\"Đen\",Công sở,basic,30,https://test.com/1.jpg\n"
        "Sản phẩm Sai Danh Mục,do_choi_tre_em,unisex,150000,\"S, M\",\"Trắng\",Chơi,test,20,https://test.com/2.jpg\n"
        "Sản phẩm Giá Âm,ao_so_mi,nu,-50000,\"S, M\",\"Hồng\",Dạo phố,test,10,https://test.com/3.jpg\n"
        "Sản phẩm Kho Âm,vay_dam,nu,450000,\"S, M\",\"Đỏ\",Dự tiệc,test,-5,https://test.com/4.jpg\n"
    ).encode("utf-8")

    files = {"file": ("mixed_products.csv", csv_content, "text/csv")}
    res = client.post("/api/admin/products/import", headers=admin_headers, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["total_rows"] == 4
    assert data["success_count"] == 1
    assert data["failed_count"] == 3
    assert len(data["errors"]) == 3

    # Kiểm tra chi tiết báo cáo lỗi theo từng dòng
    rows_with_errors = [e["row"] for e in data["errors"]]
    assert 3 in rows_with_errors  # Dòng 3 (Sản phẩm Sai Danh Mục)
    assert 4 in rows_with_errors  # Dòng 4 (Sản phẩm Giá Âm)
    assert 5 in rows_with_errors  # Dòng 5 (Sản phẩm Kho Âm)

    # Sản phẩm hợp lệ ở dòng 2 phải được lưu
    prod = next((p for p in product_service.get_all() if p.name == "Sản phẩm Hợp lệ 1"), None)
    assert prod is not None
    assert prod.price == 250000


def test_admin_products_bulk_import_upsert(admin_headers):
    """Nếu cung cấp ID đã tồn tại, thực hiện cập nhật (upsert) thay vì tạo trùng lặp."""
    # 1. Tạo trước 1 sản phẩm
    created = product_service.create_product({
        "name": "Sản phẩm Trước Khi Upsert",
        "category": "ao_khoac",
        "gender": "unisex",
        "price": 500000,
        "stock": 10,
    })
    target_id = created.id
    init_total_count = len(product_service.get_all())

    # 2. Import CSV với đúng target_id đó nhưng tên mới, giá mới, tồn kho mới
    csv_content = (
        "id,name,category,gender,price,sizes,colors,occasions,tags,stock,images\n"
        f"{target_id},Sản phẩm ĐÃ ĐƯỢC UPSERT THÀNH CÔNG,ao_khoac,unisex,650000,\"S, M, L, XL\",\"Đen\",Dạo phố,blazer,85,https://test.com/new.jpg\n"
    ).encode("utf-8")

    files = {"file": ("upsert.csv", csv_content, "text/csv")}
    res = client.post("/api/admin/products/import", headers=admin_headers, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["success_count"] == 1
    assert data["failed_count"] == 0

    # 3. Kiểm tra sản phẩm đã được cập nhật, tổng số lượng sản phẩm không tăng
    updated_prod = product_service.get_by_id(target_id)
    assert updated_prod is not None
    assert updated_prod.name == "Sản phẩm ĐÃ ĐƯỢC UPSERT THÀNH CÔNG"
    assert updated_prod.price == 650000
    assert updated_prod.stock == 85
    assert len(product_service.get_all()) == init_total_count


def test_admin_products_bulk_import_excel(admin_headers):
    """Import qua file Excel .xlsx được tạo động thành công."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"

    headers = ["name", "category", "gender", "price", "sizes", "colors", "occasions", "tags", "stock", "images"]
    ws.append(headers)

    row1 = ["Váy Lụa Excel Test 1", "vay_dam", "nu", 420000, "S, M, L", "Trắng, Be", "Dự tiệc", "lụa, sang trọng", 35, "https://test.com/excel.jpg"]
    row2 = ["Áo Khoác Dạ Excel Test 2", "ao_khoac", "unisex", 890000, "M, L, XL", "Nâu, Đen", "Thu đông", "dạ, ấm áp", 20, ""]
    ws.append(row1)
    ws.append(row2)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    files = {"file": ("import_test.xlsx", buf.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    res = client.post("/api/admin/products/import", headers=admin_headers, files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["total_rows"] == 2
    assert data["success_count"] == 2
    assert data["failed_count"] == 0

    # Kiểm tra trong kho
    names = [p.name for p in product_service.get_all()]
    assert "Váy Lụa Excel Test 1" in names
    assert "Áo Khoác Dạ Excel Test 2" in names


# ==========================================
# 2. KIỂM THỬ UPLOAD ẢNH CLOUDINARY
# ==========================================

def test_admin_upload_image_format_validation(admin_headers):
    """Chặn upload file không phải ảnh (ví dụ .txt, .pdf)."""
    fake_file = io.BytesIO(b"Hello world, not an image")
    files = {"file": ("document.txt", fake_file, "text/plain")}
    res = client.post("/api/admin/upload-image", headers=admin_headers, files=files)
    assert res.status_code == 400
    assert "định dạng" in res.json()["detail"].lower()


def test_admin_upload_image_size_validation(admin_headers):
    """Chặn upload file ảnh vượt quá 5MB."""
    # Tạo payload vượt quá 5MB
    large_payload = b"\x00" * (5 * 1024 * 1024 + 1024)
    files = {"file": ("large_image.jpg", io.BytesIO(large_payload), "image/jpeg")}
    res = client.post("/api/admin/upload-image", headers=admin_headers, files=files)
    assert res.status_code == 400
    assert "5mb" in res.json()["detail"].lower()


@patch("cloudinary.uploader.upload")
def test_admin_upload_image_cloudinary_success(mock_upload, admin_headers):
    """Upload ảnh hợp lệ lên Cloudinary thành công và trả URL + public_id."""
    mock_upload.return_value = {
        "secure_url": "https://res.cloudinary.com/jtquct7e/image/upload/v12345/aura_store/sample_shirt.jpg",
        "url": "http://res.cloudinary.com/jtquct7e/image/upload/v12345/aura_store/sample_shirt.jpg",
        "public_id": "aura_store/sample_shirt",
        "format": "jpg",
        "bytes": 204800,
    }

    dummy_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00" + b"\x00" * 100
    files = {"file": ("sample_shirt.jpg", io.BytesIO(dummy_jpeg), "image/jpeg")}
    res = client.post("/api/admin/upload-image", headers=admin_headers, files=files)
    assert res.status_code == 200
    data = res.json()
    assert "https://res.cloudinary.com" in data["url"]
    assert data["public_id"] == "aura_store/sample_shirt"
    mock_upload.assert_called_once()


@patch("cloudinary.uploader.destroy")
def test_admin_delete_image_cloudinary_success(mock_destroy, admin_headers):
    """Xóa ảnh Cloudinary qua public_id."""
    mock_destroy.return_value = {"result": "ok"}
    res = client.delete("/api/admin/images/aura_store/sample_shirt", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["success"] is True
    mock_destroy.assert_called_once_with("aura_store/sample_shirt")


# ==========================================
# 3. KIỂM THỬ LÔ HÀNG (INVENTORY BATCHES) & BÁO CÁO LÃI/LỖ
# ==========================================

def test_inventory_batch_atomic_receipt(admin_headers):
    """Nhập lô hàng mới: lưu vào inventory_batches và tăng tồn kho nguyên tử."""
    # Tạo sản phẩm kiểm thử
    p = product_service.create_product({
        "name": "Sản phẩm Thử Nghiệm Nhập Lô",
        "category": "ao_thun",
        "price": 200000,
        "stock": 20,
    })
    pid = p.id
    init_stock = p.stock

    # Nhập thêm lô 30 sản phẩm, giá vốn 90.000đ/sp
    res = client.post(
        f"/api/admin/products/{pid}/inventory",
        headers=admin_headers,
        json={"quantity": 30, "cost_price": 90000, "note": "Lô hàng tháng 9 từ xưởng may"},
    )
    assert res.status_code == 200
    batch = res.json()
    assert batch["quantity"] == 30
    assert batch["cost_price"] == 90000
    assert batch["product_id"] == pid
    assert batch["new_stock"] == init_stock + 30

    # Kiểm tra tồn kho của sản phẩm sau khi nhập
    updated_p = product_service.get_by_id(pid)
    assert updated_p.stock == init_stock + 30

    # Lấy lịch sử lô hàng của sản phẩm
    res_hist = client.get(f"/api/admin/products/{pid}/inventory", headers=admin_headers)
    assert res_hist.status_code == 200
    history = res_hist.json()
    assert len(history) >= 1
    assert any(b["note"] == "Lô hàng tháng 9 từ xưởng may" for b in history)


def test_inventory_batch_validation(admin_headers):
    """Validation cho phiếu nhập kho: số lượng > 0, giá vốn >= 0, sản phẩm phải tồn tại."""
    # Số lượng <= 0
    res1 = client.post(
        "/api/admin/products/prod_001/inventory",
        headers=admin_headers,
        json={"quantity": 0, "cost_price": 100000, "note": "Test"},
    )
    assert res1.status_code == 422

    # Giá vốn âm
    res2 = client.post(
        "/api/admin/products/prod_001/inventory",
        headers=admin_headers,
        json={"quantity": 10, "cost_price": -50000, "note": "Test"},
    )
    assert res2.status_code == 422

    # Mã sản phẩm không tồn tại -> 404
    res3 = client.post(
        "/api/admin/products/prod_non_existent_9999/inventory",
        headers=admin_headers,
        json={"quantity": 10, "cost_price": 100000, "note": "Test"},
    )
    assert res3.status_code == 404


def test_profit_loss_report_weighted_average_cost(admin_headers):
    """Kiểm tra công thức Giá vốn bình quân gia quyền (WAC) và báo cáo Lãi/Lỗ."""
    # 1. Tạo sản phẩm riêng cho test WAC
    p = product_service.create_product({
        "name": "Sản phẩm Tính Giá Vốn BQGQ",
        "category": "ao_so_mi",
        "price": 300000,
        "stock": 0,
    })
    pid = p.id

    # 2. Nhập 2 lô hàng với giá vốn khác nhau:
    # Lô 1: 10 cái @ 100.000đ = 1.000.000đ
    # Lô 2: 10 cái @ 200.000đ = 2.000.000đ
    # Tổng: 20 cái, tổng vốn: 3.000.000đ -> WAC = 3.000.000 / 20 = 150.000đ
    client.post(f"/api/admin/products/{pid}/inventory", headers=admin_headers, json={"quantity": 10, "cost_price": 100000, "note": "Lô 1"})
    client.post(f"/api/admin/products/{pid}/inventory", headers=admin_headers, json={"quantity": 10, "cost_price": 200000, "note": "Lô 2"})

    # Kiểm tra WAC được tính đúng bằng 150.000đ
    wac = db_service.get_product_weighted_average_cost(pid)
    assert wac == 150000.0

    # 3. Tạo 1 đơn hàng đã thanh toán chứa 2 sản phẩm này
    # Doanh thu = 2 * 300.000đ = 600.000đ
    # Giá vốn COGS = 2 * 150.000đ = 300.000đ
    # Lợi nhuận gộp = 600.000đ - 300.000đ = 300.000đ
    order_id = f"AURA-TEST-PROFIT-{pid}"
    order_data = {
        "order_id": order_id,
        "status": "confirmed",
        "payment_method": "qr_transfer",
        "customer": {"name": "Khách Test Lãi Lỗ", "phone": "0987654321", "address": "TP.HCM"},
        "quote": {
            "total": 600000,
            "subtotal": 600000,
            "lines": [
                {
                    "product_id": pid,
                    "name": p.name,
                    "color": "Tiêu chuẩn",
                    "size": "M",
                    "quantity": 2,
                    "unit_price": 300000,
                    "line_total": 600000,
                }
            ]
        }
    }
    db_service.save_order(order_data)
    db_service.confirm_payment(order_id, 600000, f"TX-{order_id}", "vietqr")

    # 4. Gọi API báo cáo lãi lỗ
    res = client.get("/api/admin/reports/profit", headers=admin_headers)
    assert res.status_code == 200
    report = res.json()

    assert "total_revenue" in report
    assert "total_cogs" in report
    assert "gross_profit" in report
    assert "profit_margin_percent" in report
    assert "products_breakdown" in report

    # Tìm sản phẩm vừa tạo trong breakdown
    item = next((x for x in report["products_breakdown"] if x["product_id"] == pid), None)
    assert item is not None
    assert item["sold_quantity"] == 2
    assert item["revenue"] == 600000
    assert item["cost_price_wac"] == 150000.0
    assert item["cogs"] == 300000
    assert item["profit"] == 300000
    assert item["margin_percent"] == 50.0


# ==========================================
# 4. PHÂN QUYỀN RBAC (CHẶN USER THƯỜNG)
# ==========================================

def test_non_admin_forbidden_endpoints(user_headers):
    """User thường không được phép gọi các API quản trị này (403 Forbidden)."""
    # 1. Chặn Import
    res_import = client.post("/api/admin/products/import", headers=user_headers, files={"file": ("test.csv", b"dummy", "text/csv")})
    assert res_import.status_code == 403

    # 2. Chặn Upload ảnh
    res_upload = client.post("/api/admin/upload-image", headers=user_headers, files={"file": ("test.jpg", b"dummy", "image/jpeg")})
    assert res_upload.status_code == 403

    # 3. Chặn Nhập kho
    res_inv = client.post("/api/admin/products/prod_001/inventory", headers=user_headers, json={"quantity": 10, "cost_price": 50000})
    assert res_inv.status_code == 403

    # 4. Chặn Báo cáo lãi lỗ
    res_profit = client.get("/api/admin/reports/profit", headers=user_headers)
    assert res_profit.status_code == 403
