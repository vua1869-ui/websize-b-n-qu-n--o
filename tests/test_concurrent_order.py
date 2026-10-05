import concurrent.futures
import threading
import time
import pytest

from app.models.schemas import OrderCreateRequest, OrderItem
from app.services.order_service import OrderError, order_service
from app.services.product_service import product_service


def test_concurrent_purchase_race_condition_proof():
    """
    Mô phỏng 2 khách hàng đồng thời gọi đặt mua khi sản phẩm chỉ còn 1 cái trong kho.
    Ngay cả khi 2 thread gọi build_quote() cùng lúc, nhờ cơ chế Atomic Lock & DB Transaction,
    chỉ đúng 1 đơn thành công, đơn thứ 2 sẽ nhận 409 Conflict ("hết hàng").
    """
    p = product_service.get_by_id("prod_001")
    assert p is not None
    p.stock = 1
    test_size = p.sizes[0]
    test_color = p.colors[0].name

    barrier = threading.Barrier(2)
    original_build_quote = order_service.build_quote

    def build_quote_with_barrier(*args, **kwargs):
        res = original_build_quote(*args, **kwargs)
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass
        return res

    order_service.build_quote = build_quote_with_barrier

    req1 = OrderCreateRequest(
        customer_name="Khách Hàng A",
        customer_phone="0987654321",
        customer_address="123 Đường ABC, Phường 1, TP.HCM",
        payment_method="cod",
        items=[OrderItem(product_id=p.id, size=test_size, color=test_color, quantity=1)]
    )

    req2 = OrderCreateRequest(
        customer_name="Khách Hàng B",
        customer_phone="0912345678",
        customer_address="456 Đường XYZ, Phường 2, TP.HCM",
        payment_method="cod",
        items=[OrderItem(product_id=p.id, size=test_size, color=test_color, quantity=1)]
    )

    results = []
    errors = []

    def place_order(req):
        try:
            res = order_service.create_order(req)
            results.append(res)
        except OrderError as e:
            errors.append(e)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(place_order, req1)
            f2 = executor.submit(place_order, req2)
            concurrent.futures.wait([f1, f2])

        assert len(results) == 1, f"Chỉ được 1 đơn thành công, thực tế: {len(results)}"
        assert len(errors) == 1, f"Chỉ được 1 đơn bị từ chối, thực tế: {len(errors)}"
        assert errors[0].status_code in (400, 409)
        assert p.stock == 0, f"Tồn kho phải bằng 0, thực tế là {p.stock}"
    finally:
        order_service.build_quote = original_build_quote


def test_concurrent_multiple_buyers_stock_exhaustion():
    """
    Mô phỏng 5 khách hàng đồng thời đặt mua sản phẩm có tồn kho là 2.
    Đảm bảo đúng 2 đơn thành công, 3 đơn bị từ chối, tồn kho bằng 0.
    """
    p = product_service.get_by_id("prod_002")
    assert p is not None
    p.stock = 2
    test_size = p.sizes[0]
    test_color = p.colors[0].name

    results = []
    errors = []

    def place_order(idx):
        req = OrderCreateRequest(
            customer_name=f"Khách Hàng {idx}",
            customer_phone=f"09876543{idx:02d}",
            customer_address=f"{idx} Đường Lê Lợi, TP.HCM",
            payment_method="cod",
            items=[OrderItem(product_id=p.id, size=test_size, color=test_color, quantity=1)]
        )
        try:
            res = order_service.create_order(req)
            results.append(res)
        except OrderError as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(place_order, i) for i in range(5)]
        concurrent.futures.wait(futures)

    assert len(results) == 2, f"Chính xác 2 đơn thành công, thực tế: {len(results)}"
    assert len(errors) == 3, f"Chính xác 3 đơn bị từ chối, thực tế: {len(errors)}"
    assert p.stock == 0, f"Tồn kho còn lại phải là 0, thực tế: {p.stock}"
