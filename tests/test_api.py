from app.config import settings
from app.services.order_service import make_combo_token
from app.services.product_service import product_service, flash_sale_window
from tests.conftest import CUSTOMER, item


# ---------- Trang & dữ liệu cơ bản ----------
def test_home_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AURA STUDIO" in r.text


def test_flash_sale_filter_returns_items(client):
    """Lỗi cũ: giao diện gửi category=flash_sale khiến kết quả rỗng."""
    r = client.get("/api/products", params={"category": "flash_sale", "flash_sale_only": "true"})
    assert r.status_code == 200 and len(r.json()) == len(product_service.get_flash_sale_products())
    assert all(p["flash_sale"] for p in r.json())


def test_effective_price_uses_flash_price(client):
    p = client.get("/api/products/prod_001").json()
    assert p["final_price"] == 429000 and p["price"] == 489000
    p = client.get("/api/products/prod_004").json()  # không flash sale
    assert p["final_price"] == p["price"]


def test_search_accent_insensitive(client):
    with_accent = {p["id"] for p in client.get("/api/products", params={"search": "áo thun"}).json()}
    without = {p["id"] for p in client.get("/api/products", params={"search": "ao thun"}).json()}
    assert with_accent == without and with_accent


def test_categories_have_consistent_names(client):
    cats = {c["id"]: c for c in client.get("/api/categories").json()}
    expected = sum(1 for p in product_service.get_all() if p.category == "ao_khoac")
    assert cats["ao_khoac"]["count"] == expected and cats["ao_khoac"]["name"] == "Áo Khoác & Len"


def test_flash_sale_window_is_in_future_and_max_3h():
    now, end = flash_sale_window()
    assert 0 < end - now <= 3 * 3600 * 1000


# ---------- Đơn hàng: server là nguồn sự thật về giá ----------
def test_client_cannot_set_price(client):
    """Lỗi cũ: gửi price=1 vẫn được duyệt 1đ."""
    body = {**CUSTOMER, "items": [{**item("prod_001"), "price": 1}]}
    r = client.post("/api/orders", json=body)
    assert r.status_code == 200
    assert r.json()["quote"]["subtotal"] == 429000  # giá thật, bỏ qua 'price' của client


def test_negative_or_zero_quantity_rejected(client):
    for q in (-3, 0):
        r = client.post("/api/orders", json={**CUSTOMER, "items": [item(qty=q)]})
        assert r.status_code == 422


def test_unknown_product_size_color_rejected(client):
    bad = client.post("/api/orders", json={**CUSTOMER, "items": [{**item(), "product_id": "prod_999"}]})
    assert bad.status_code == 400
    bad = client.post("/api/orders", json={**CUSTOMER, "items": [{**item(), "size": "XXXL"}]})
    assert bad.status_code == 400
    bad = client.post("/api/orders", json={**CUSTOMER, "items": [{**item(), "color": "Hồng Neon"}]})
    assert bad.status_code == 400


def test_phone_validation(client):
    r = client.post("/api/orders", json={**CUSTOMER, "customer_phone": "123", "items": [item()]})
    assert r.status_code == 422 and "điện thoại" in r.json()["detail"].lower()


def test_shipping_fee_and_free_threshold(client):
    cheap = client.post("/api/orders/quote", json={"items": [item("prod_015")]}).json()  # 79k
    assert cheap["shipping_fee"] == settings.SHIPPING_FEE
    assert cheap["total"] == 79000 + settings.SHIPPING_FEE
    big = client.post("/api/orders/quote", json={"items": [item("prod_001")]}).json()  # 429k
    assert big["shipping_fee"] == 0


def test_voucher_rules(client):
    q = lambda code, items: client.post("/api/orders/quote", json={"items": items, "voucher_code": code}).json()
    # AURA10: 10% nhưng tối đa 50k
    r = q("aura10", [item("prod_008", qty=3)])  # 3 x 699k = 2.097M -> 10% = 209k -> trần 50k
    assert r["voucher_discount"] == 50000
    # Đơn chưa đủ tối thiểu
    r = q("AURA50K", [item("prod_015")])
    assert r["voucher_discount"] == 0 and "299" in r["voucher_message"]
    # Mã không tồn tại
    r = q("HACK100", [item()])
    assert r["voucher_discount"] == 0 and "không tồn tại" in r["voucher_message"]
    # FREESHIP xóa phí ship
    r = q("FREESHIP", [item("prod_015")])
    assert r["shipping_fee"] == 30000 and r["shipping_discount"] == 30000
    assert r["total"] == 79000


def test_invalid_voucher_blocks_order_instead_of_silent_ignore(client):
    r = client.post("/api/orders", json={**CUSTOMER, "items": [item()], "voucher_code": "NOPE"})
    assert r.status_code == 400 and "không tồn tại" in r.json()["detail"]


def test_total_never_negative(client):
    r = client.post("/api/orders/quote", json={"items": [item("prod_015")], "voucher_code": "FREESHIP"}).json()
    assert r["total"] >= 0


# ---------- Combo AI phối đồ (trước đây chỉ hiển thị "giảm 15%" nhưng không giảm) ----------
def test_combo_discount_applies_with_valid_token(client):
    outfit = client.post("/api/ai/outfit", json={"product_id": "prod_001"}).json()
    items = [item(i["id"], combo_token=outfit["combo_token"]) for i in outfit["items"]]
    q = client.post("/api/orders/quote", json={"items": items}).json()
    expected_total = sum(i["final_price"] for i in outfit["items"])
    assert q["subtotal"] == expected_total
    assert q["combo_discount"] == expected_total * settings.COMBO_DISCOUNT_PERCENT // 100
    assert all(l["combo"] for l in q["lines"])


def test_combo_token_cannot_be_forged_or_reused_partially(client):
    # token tự chế
    q = client.post("/api/orders/quote", json={"items": [
        item("prod_001", combo_token="deadbeef"), item("prod_003", combo_token="deadbeef")]}).json()
    assert q["combo_discount"] == 0
    # token hợp lệ của bộ (001,003,017) nhưng giỏ chỉ có 2 trong 3 món -> không giảm
    tok = make_combo_token(["prod_001", "prod_003", "prod_017"])
    q = client.post("/api/orders/quote", json={"items": [
        item("prod_001", combo_token=tok), item("prod_003", combo_token=tok)]}).json()
    assert q["combo_discount"] == 0


# ---------- Kho hàng & lưu đơn ----------
def test_stock_decrements_and_blocks_oversell(client):
    before = client.get("/api/products/prod_008").json()["stock"]  # 19
    r = client.post("/api/orders", json={**CUSTOMER, "items": [item("prod_008", qty=5)]})
    assert r.status_code == 200
    after = client.get("/api/products/prod_008").json()
    assert after["stock"] == before - 5 and after["sold_count"] >= 5
    # vượt quá tồn kho
    r = client.post("/api/orders", json={**CUSTOMER, "items": [item("prod_008", qty=10), item("prod_008", size="L", qty=10)]})
    assert r.status_code == 409 and "chỉ còn" in r.json()["detail"]
    assert client.get("/api/products/prod_008").json()["stock"] == before - 5  # không bị trừ oan


def test_order_is_persisted_and_stock_restored_after_restart(client):
    from app.services.order_service import order_service
    client.post("/api/orders", json={**CUSTOMER, "items": [item("prod_008", qty=2)]})
    assert order_service.count_orders() == 1
    product_service._load_products()  # giả lập khởi động lại: kho về số gốc
    assert product_service.get_by_id("prod_008").stock == 19
    order_service._restore_stock_from_history()
    assert product_service.get_by_id("prod_008").stock == 17


def test_qr_transfer_order_is_pending_payment(client):
    r = client.post("/api/orders", json={**CUSTOMER, "items": [item()], "payment_method": "qr_transfer"}).json()
    assert r["status"] == "pending_payment" and r["order_id"] in r["message"]


# ---------- Tính size ----------
def test_size_maps_to_numeric_jeans_sizes(client):
    r = client.post("/api/ai/size-recommend", json={"height_cm": 170, "weight_kg": 65, "product_id": "prod_006"}).json()
    assert r["recommended_size"] in r["available_sizes"]  # trước đây trả 'M' dù quần chỉ có 28–34


def test_size_freesize_and_letter_products(client):
    r = client.post("/api/ai/size-recommend", json={"height_cm": 170, "weight_kg": 60, "product_id": "prod_014"}).json()
    assert r["recommended_size"].lower().startswith("free")
    r = client.post("/api/ai/size-recommend", json={"height_cm": 165, "weight_kg": 50, "gender": "nu", "product_id": "prod_002"}).json()
    assert r["recommended_size"] in ("S", "M", "L")


def test_size_rejects_invalid_body(client):
    """Lỗi cũ: height=0 gây lỗi 500 (chia cho 0)."""
    r = client.post("/api/ai/size-recommend", json={"height_cm": 0, "weight_kg": 60})
    assert r.status_code == 422


def test_size_is_monotonic_in_weight(client):
    order = ["S", "M", "L", "XL", "XXL"]
    idx = [order.index(client.post("/api/ai/size-recommend", json={"height_cm": 170, "weight_kg": w, "gender": "nam"}).json()["recommended_size"])
           for w in (45, 55, 63, 70, 78, 95)]
    assert idx == sorted(idx)


# ---------- Phối đồ ----------
def test_outfit_deterministic_and_variant_changes(client):
    a = client.post("/api/ai/outfit", json={"product_id": "prod_005"}).json()
    b = client.post("/api/ai/outfit", json={"product_id": "prod_005"}).json()
    assert [i["id"] for i in a["items"]] == [i["id"] for i in b["items"]]  # trước đây dùng hash() ngẫu nhiên mỗi lần chạy
    assert a["items"][0]["id"] == "prod_005" and len(a["items"]) >= 3
    c = client.post("/api/ai/outfit", json={"product_id": "prod_005", "variant": 1}).json()
    assert [i["id"] for i in c["items"]] != [i["id"] for i in a["items"]]


def test_outfit_respects_gender(client):
    r = client.post("/api/ai/outfit", json={"product_id": "prod_016"}).json()  # sơ mi nam
    assert all(i["gender"] in ("nam", "unisex") for i in r["items"])


def test_outfit_unknown_product_404(client):
    assert client.post("/api/ai/outfit", json={"product_id": "nope"}).status_code == 404


# ---------- Chat AI (bộ luật nội bộ, không cần Ollama/Gemini) ----------
def _chat(client, msg, **kw):
    return client.post("/api/ai/chat", json={"user_message": msg, "engine": "rules", **kw}).json()


def test_chat_scenarios_and_no_ids_in_text(client):
    r = _chat(client, "Tư vấn đồ đi phỏng vấn")
    assert r["recommended_products"] and "prod_" not in r["reply"]
    r = _chat(client, "di bien mua he mac gi")  # gõ không dấu
    assert any(p["id"] == "prod_018" for p in r["recommended_products"])


def test_chat_scenarios_pull_extra_products_from_catalog(client):
    """Kịch bản không chỉ gợi ý các mã gắn cứng: món khớp dịp trong danh mục cũng được đưa vào."""
    from app.services.ai_service import SCENARIOS
    curated = {pid for pid, _ in next(s for s in SCENARIOS if s["key"] == "di_bien")["picks"]}
    ids = [p["id"] for p in _chat(client, "đồ đi biển")["recommended_products"]]
    assert "prod_018" in ids and len(ids) == len(set(ids)) <= 4
    assert set(ids) - curated

def test_season_trend_mid_season_shows_hot_products(client, monkeypatch):
    import datetime as dt
    from app.services import season_service as mod

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 23)  # giữa mùa Thu Đông, còn nhiều ngày mới hết mùa

    monkeypatch.setattr(mod, "dt", type("M", (), {"date": FakeDate, "timedelta": dt.timedelta}))
    r = _chat(client, "mùa này có gì hot")
    assert r["recommended_products"]
    assert "Thu Đông" in r["reply"] and "xả" not in r["reply"].lower()


def test_season_trend_switches_to_clearance_near_season_end(client, monkeypatch):
    import datetime as dt
    from app.services import season_service as mod

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 1, 25)  # còn 6 ngày là hết mùa Thu Đông

    monkeypatch.setattr(mod, "dt", type("M", (), {"date": FakeDate, "timedelta": dt.timedelta}))
    r = _chat(client, "mùa này có gì hot")
    assert r["recommended_products"]
    assert "xả" in r["reply"].lower() and "%" in r["reply"]


def test_clearance_keyword_works_regardless_of_date(client):
    r = _chat(client, "shop nên làm gì để xả hàng cuối mùa")
    assert r["recommended_products"]
    assert "đề xuất giảm" in r["reply"]
    ids = [p["id"] for p in r["recommended_products"]]
    assert len(ids) == len(set(ids))

def test_chat_free_text_respects_gender(client):
    r = _chat(client, "quần jeans nam")
    assert r["recommended_products"]
    assert all(p["gender"] in ("nam", "unisex") for p in r["recommended_products"])


def test_chat_size_with_body_numbers(client):
    r = _chat(client, "mình cao 1m65 nặng 55kg mặc size gì")
    assert "size" in r["reply"].lower() and "55" in r["reply"]


def test_chat_word_boundary_no_false_positive(client):
    """'m' trong 'mình' không được kích hoạt nhánh tư vấn size."""
    r = _chat(client, "cho mình hỏi shop ở đâu")
    assert "chiều cao" not in r["reply"].lower()


def test_chat_budget(client):
    r = _chat(client, "áo dưới 200k")
    assert r["recommended_products"] and all(p["final_price"] <= 200000 for p in r["recommended_products"])


def test_chat_context_product(client):
    r = _chat(client, "phối với gì", context_product_id="prod_001")
    assert r["recommended_products"][0]["id"] == "prod_001"


def test_chat_rejects_empty_and_huge_messages(client):
    assert client.post("/api/ai/chat", json={"user_message": ""}).status_code == 422
    assert client.post("/api/ai/chat", json={"user_message": "x" * 5000}).status_code == 422


def test_chat_llm_output_ids_become_cards_and_are_stripped(client, monkeypatch):
    from app.services import ai_service as mod
    monkeypatch.setattr(mod.settings, "GEMINI_API_KEY", "fake")
    monkeypatch.setattr(mod.ai_service, "_call_gemini",
                        lambda *a, **k: "Bạn thử **Blazer Linen** [[prod_001]] cùng quần tây (prod_003) nhé!")
    r = client.post("/api/ai/chat", json={"user_message": "tư vấn giúp"}).json()
    assert [p["id"] for p in r["recommended_products"]] == ["prod_001", "prod_003"]
    assert "prod_" not in r["reply"] and "[[" not in r["reply"]
    assert "Gemini" in r["engine_used"]


def test_chat_falls_back_to_rules_when_llm_fails(client, monkeypatch):
    from app.services import ai_service as mod
    monkeypatch.setattr(mod.settings, "GEMINI_API_KEY", "fake")
    def boom(*a, **k): raise OSError("network down")
    monkeypatch.setattr(mod.ai_service, "_call_gemini", boom)
    monkeypatch.setattr(mod.ai_service, "_call_ollama", boom)
    mod.ai_service._down_until.clear()
    r = client.post("/api/ai/chat", json={"user_message": "đồ đi biển"}).json()
    assert "nội bộ" in r["engine_used"] and r["recommended_products"]


def test_live_comment_uses_real_size_and_prices(client):
    r = client.post("/api/ai/live-comment", json={"user_name": "Linh", "comment": "1m62 52kg mặc size gì ạ"}).json()
    assert "size" in r["reply"].lower() and r["pinned_product"]
    r = client.post("/api/ai/live-comment", json={"user_name": "Linh", "comment": "cho mình hỏi blazer"}).json()
    assert "429.000" in r["reply"]  # giá thật, không viết cứng


def test_no_secrets_committed():
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in list(root.glob(".env*")) + list(root.rglob("*.py")):
        if f.name == "test_api.py":
            continue
        assert not re.search(r"AIza[0-9A-Za-z_\-]{20,}|AQ\.[A-Za-z0-9_\-]{30,}", f.read_text(encoding="utf-8", errors="ignore")), f


# ---------- Tích hợp Ollama thật (dùng server Ollama giả để kiểm chứng giao thức) ----------
def test_ollama_integration_with_history_and_fallback(client, monkeypatch):
    import json, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from app.services import ai_service as mod

    seen = {}

    class FakeOllama(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["body"] = body
            out = json.dumps({"message": {"role": "assistant",
                              "content": "Mình gợi ý **Blazer Linen** [[prod_001]] và quần tây [[prod_003]]."}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(out)
        def log_message(self, *a): pass

    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(mod.settings, "OLLAMA_HOST", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(mod.settings, "GEMINI_API_KEY", "")
    mod.ai_service._down_until.clear()
    try:
        r = client.post("/api/ai/chat", json={
            "user_message": "còn màu nào không?",
            "messages": [{"role": "assistant", "content": "Chào bạn"},           # lời chào: phải bị bỏ
                         {"role": "user", "content": "tôi đi làm văn phòng"},
                         {"role": "assistant", "content": "Bạn thử blazer nhé"}],
        }).json()
    finally:
        srv.shutdown()
    assert "Ollama" in r["engine_used"]
    assert [p["id"] for p in r["recommended_products"]] == ["prod_001", "prod_003"]
    assert "[[" not in r["reply"] and "prod_" not in r["reply"]
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]          # có lịch sử, bỏ lời chào mở đầu
    assert "prod_001" in seen["body"]["messages"][0]["content"]       # catalog thật nằm trong system prompt

    # Ollama tắt -> rơi về bộ luật ngay lập tức, và nhớ trạng thái lỗi (không chờ timeout ở lượt sau)
    monkeypatch.setattr(mod.settings, "OLLAMA_HOST", "http://127.0.0.1:1")
    mod.ai_service._down_until.clear()
    r = client.post("/api/ai/chat", json={"user_message": "đồ đi biển"}).json()
    assert "nội bộ" in r["engine_used"] and "ollama" in mod.ai_service._down_until
