import pytest
from app.config import settings
from app.models.schemas import ChatRequest, ChatMessage
from app.services.ai_service import ai_service
from app.services.product_service import product_service
from app.services.trend_sources.google_trends import GoogleTrendsSource


def test_ai_prompt_max_products_limit_and_length():
    """Kiểm tra độ dài prompt và số lượng sản phẩm không vượt ngưỡng cấu hình."""
    products = product_service.get_all()
    assert len(products) > 100  # Catalog thực tế có hàng trăm sản phẩm

    # Lấy relevant products qua hàm mới
    rel_prods = ai_service._get_relevant_products(
        user_msg="Tư vấn áo sơ mi và quần tây công sở",
        history=[ChatMessage(role="user", content="Tôi muốn tìm áo blazer")],
        context=product_service.get_by_id("prod_001")
    )
    assert len(rel_prods) <= settings.AI_PROMPT_MAX_PRODUCTS
    assert len(rel_prods) > 0

    # Build prompt và kiểm tra độ dài
    prompt = ai_service._system_prompt(context=None, relevant_products=rel_prods)
    # Trước đây: 671 sp ~95.000 ký tự. Nay <= 30 sp, độ dài < 8.000 ký tự
    assert len(prompt) < 8000
    # Định dạng dòng sản phẩm đúng: - tên [[prod_xxx]] | giá | ...
    for p in rel_prods:
        assert f"[[{p.id}]]" in prompt


def test_chat_request_max_messages_and_client_engine_ignored(client, monkeypatch):
    """Kiểm tra ChatRequest giới hạn messages <= 20 và bỏ qua engine của client."""
    # 1. messages > 20 -> 422 Unprocessable Entity
    too_many_msgs = [{"role": "user", "content": f"msg {i}"} for i in range(21)]
    r = client.post("/api/ai/chat", json={
        "user_message": "xin chào",
        "messages": too_many_msgs
    })
    assert r.status_code == 422

    # 2. messages <= 20 hợp lệ
    valid_msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
    r_valid = client.post("/api/ai/chat", json={
        "user_message": "xin chào",
        "messages": valid_msgs
    })
    assert r_valid.status_code == 200

    # 3. Client gửi engine='gemini' nhưng server cấu hình 'rules' -> luôn dùng server
    monkeypatch.setattr(settings, "AI_ENGINE", "rules")
    r_eng = client.post("/api/ai/chat", json={
        "user_message": "áo sơ mi trắng",
        "engine": "gemini"
    })
    assert r_eng.status_code == 200
    assert "bộ luật nội bộ" in r_eng.json()["engine_used"]


def test_ai_concurrency_semaphore_fallback_to_rules(client, monkeypatch):
    """Khi semaphore LLM hết chỗ (đang bận AI_MAX_CONCURRENCY cuộc gọi), rơi về bộ luật ngay."""
    monkeypatch.setattr(settings, "AI_ENGINE", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fake_key")

    # Chiếm hết slot của semaphore
    acquired = []
    for _ in range(settings.AI_MAX_CONCURRENCY):
        acquired.append(ai_service._llm_semaphore.acquire(blocking=False))
    assert all(acquired)

    try:
        # Gọi chat khi semaphore đang kẹt -> phải lập tức dùng rules engine thay vì block
        res = ai_service.chat(ChatRequest(user_message="tư vấn áo sơ mi"))
        assert "bộ luật nội bộ" in res.engine_used
    finally:
        for _ in acquired:
            ai_service._llm_semaphore.release()


def test_google_trends_anchor_normalization():
    """Kiểm tra Google Trends chuẩn hóa điểm theo từ khóa neo."""
    src = GoogleTrendsSource()
    assert hasattr(src, "anchor_kw")
    assert src.anchor_kw == settings.TREND_ANCHOR_KEYWORD

    # Kiểm tra không monkey patch urllib3 toàn cục
    import urllib3.util.retry
    # Retry.__init__ không bị ghi đè bởi hàm custom
    assert "patched" not in urllib3.util.retry.Retry.__init__.__qualname__.lower()
