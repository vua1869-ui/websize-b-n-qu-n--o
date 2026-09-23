import json
import re
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

from app.config import settings
from app.models.schemas import (
    ChatMessage, ChatRequest, ChatResponse, LiveCommentResponse, Product,
)
from app.services.outfit_service import SLOT_OF, outfit_service
from app.services.product_service import product_service
from app.services.text_utils import has_any, has_word, normalize
from app.services.season_service import (CLEARANCE_WINDOW_DAYS, clearance_candidates, current_season,
                                          hot_this_season, suggested_discount)
from app.services.trend_service import trend_service

ID_TOKEN_RE = re.compile(r"\[\[\s*(prod_\d{3})\s*\]\]")
BARE_ID_RE = re.compile(r"\(?\b(prod_\d{3})\b\)?")

DEFAULT_SUGGESTIONS = [
    "💼 Tư vấn đồ đi làm thanh lịch",
    "🥂 Set đồ đi tiệc sang trọng",
    "🌊 Trang phục đi biển mùa hè",
    "☕ Outfit hẹn hò cuối tuần",
    "📏 Cao 1m65 nặng 55kg mặc size gì?",
]


def short_name(p: Product, words: int = 5) -> str:
    return " ".join(p.name.split()[:words])


def money(n: int) -> str:
    return f"{n:,}".replace(",", ".") + "đ"


def parse_body(text: str) -> Tuple[Optional[float], Optional[float]]:
    """Đọc chiều cao/cân nặng từ câu tự nhiên: '1m62 52kg', '165cm 60 kg'."""
    t = normalize(text)
    height = weight = None
    m = re.search(r"\b([12])\s*m\s*(\d{2})\b", t)          # 1m62
    if m:
        height = int(m.group(1)) * 100 + int(m.group(2))
    else:
        m = re.search(r"\b(1[3-9]\d|2[0-2]\d)\s*cm\b", t)    # 165cm
        if m:
            height = int(m.group(1))
        else:
            m = re.search(r"\b([12])[.,](\d{1,2})\s*m\b", t)  # 1.65m
            if m:
                height = round(float(f"{m.group(1)}.{m.group(2)}") * 100)
    m = re.search(r"\b(\d{2,3})\s*(?:kg|ky|kilo|can)\b", t)
    if m:
        weight = int(m.group(1))
    if height and not (100 <= height <= 230):
        height = None
    if weight and not (25 <= weight <= 200):
        weight = None
    return height, weight


def guess_gender(text_norm: str) -> str:
    # Lưu ý: sau khi bỏ dấu, "có"/"chỉ"/"ảnh" trở thành co/chi/anh nên KHÔNG dùng làm tín hiệu giới tính.
    if has_any(text_norm, ["nu", "con gai", "phu nu", "ban gai", "cho nu"]):
        return "nu"
    if has_any(text_norm, ["nam", "con trai", "dan ong", "ban trai", "cho nam"]):
        return "nam"
    return "unisex"


# ------------------------------------------------------------------ kịch bản tư vấn
# picks: (mã sản phẩm, lý do chọn). Thứ tự = mức ưu tiên.
SCENARIOS: List[Dict] = [
    dict(key="cong_so", emoji="👔", title="Set đồ công sở & phỏng vấn",
         kw=["phong van", "cong so", "di lam", "van phong", "lich su", "hop", "gap doi tac"],
         intro="Để tạo ấn tượng chuyên nghiệp mà vẫn hiện đại, mình gợi ý phong cách Smart Casual:",
         picks=[("prod_001", "tạo phong thái đĩnh đạc, form suông thoáng nhẹ"),
                ("prod_003", "ống suông kéo dài đôi chân, đứng form cả ngày"),
                ("prod_002", "chất satin mềm, lịch sự cho nữ"),
                ("prod_016", "kẻ sọc Oxford chống nhăn, nam tính"),
                ("prod_009", "chân váy midi lưng cao, gọn gàng công sở"),
                ("prod_014", "túi tote đựng vừa laptop 14 inch")]),
    dict(key="du_tiec", emoji="🥂", title="Outfit dạ tiệc & đám cưới",
         kw=["dam cuoi", "tiec", "da hoi", "sang trong", "party", "su kien", "cuoi"],
         intro="Với tiệc tối hay đám cưới, quy tắc hàng đầu là quý phái và tinh tế:",
         picks=[("prod_004", "lụa rủ mềm, hở lưng quyến rũ, tôn đường cong"),
                ("prod_001", "blazer khoác ngoài cho phong cách menswear sang chảnh"),
                ("prod_003", "quần tây xếp ly đi cùng blazer"),
                ("prod_017", "thắt lưng da khóa vàng làm điểm nhấn"),
                ("prod_015", "kính mát nếu tiệc ngoài trời")]),
    dict(key="di_bien", emoji="🌊", title="Trang phục đi biển & du lịch",
         kw=["bien", "mua he", "du lich", "resort", "nghi duong", "da ngoai", "nang"],
         intro="Đi biển thì ưu tiên thoáng mát, thoải mái và lên hình đẹp:",
         picks=[("prod_018", "họa tiết lá nhiệt đới tươi mát, chất lụa nhẹ"),
                ("prod_012", "đầm hoa nhí nàng thơ dạo bước trên bãi cát"),
                ("prod_013", "quần short đũi thoáng, mặc cả đi biển lẫn dạo phố"),
                ("prod_005", "áo thun cotton boxy phối cùng quần short"),
                ("prod_015", "kính mát UV400, phụ kiện không thể thiếu")]),
    dict(key="hen_ho", emoji="☕", title="Outfit hẹn hò & cà phê cuối tuần",
         kw=["hen ho", "date", "nguoi yeu", "cafe", "ca phe", "dao pho", "cuoi tuan"],
         intro="Set đồ nhẹ nhàng, gọn gàng giúp buổi hẹn thêm ấn tượng:",
         picks=[("prod_012", "đầm hoa nhí tay bồng, dịu dàng"),
                ("prod_009", "chân váy chữ A xếp ly, phối với sơ mi lụa"),
                ("prod_007", "polo dệt kim phong cách Old Money cho nam"),
                ("prod_006", "jeans baggy vintage, dễ phối"),
                ("prod_014", "túi tote da làm điểm nhấn")]),
    dict(key="thu_dong", emoji="❄️", title="Thu đông ấm áp & đi Đà Lạt",
         kw=["dong", "thu dong", "lanh", "am", "da lat", "sapa", "ret"],
         intro="Mùa lạnh hợp nhất với nghệ thuật phối nhiều lớp:",
         picks=[("prod_008", "trench coat dáng dài, cản gió, sang trọng"),
                ("prod_010", "cardigan dệt thô mặc ngoài áo thun/sơ mi rất ấm cúng"),
                ("prod_011", "hoodie nỉ bông êm ái cho buổi tối dạo phố"),
                ("prod_003", "quần tây ống suông mặc kèm áo khoác dài")]),
    dict(key="streetwear", emoji="🛹", title="Streetwear cá tính",
         kw=["streetwear", "ca tinh", "nang dong", "ngau", "tre trung", "boxy", "oversize"],
         intro="Công thức đường phố cực chất:",
         picks=[("prod_005", "áo thun 260GSM dày dặn, đứng form vai"),
                ("prod_006", "jeans baggy wash retro 90s"),
                ("prod_011", "hoodie oversize phối layer"),
                ("prod_015", "kính retro hoàn thiện set")]),
]

# Kịch bản -> mã dịp (occasions) / tag trong products.json.
# Dùng để tự lấy thêm sản phẩm mới của danh mục vào câu trả lời, không phải sửa code mỗi lần thêm hàng.
SCENARIO_OCCASIONS: Dict[str, Set[str]] = {
    "cong_so": {"cong_so", "phong_van", "di_lam", "gap_doi_tac"},
    "du_tiec": {"du_tiec", "dam_cuoi", "hen_ho_cao_cap"},
    "di_bien": {"di_bien", "nghi_duong", "mua_he"},
    "hen_ho": {"hen_ho", "cafe", "chup_anh"},
    "thu_dong": {"thu_dong", "mua_dong", "du_lich_da_lat"},
    "streetwear": set(),  # streetwear chỉ khớp theo tag (dao_pho/di_choi quá chung, dễ ra phụ kiện lạc tông)
}
SCENARIO_TAGS: Dict[str, Set[str]] = {"streetwear": {"streetwear"}}
SCENARIO_ANCHORS = 2  # số món "kinh điển" (đã chọn tay ở picks) luôn giữ lại; các món còn lại lấy tự động

# Hỏi xu hướng thời trang hiện nay -> lấy dữ liệu thật từ trend_service (AI Trend Detection)
FASHION_TREND_KW = [
    "xu huong", "trend", "hot trend", "trend gi", "thoi trang hot",
    "xu huong thoi trang", "phong cach hot", "dang hot", "co trend gi",
    "xu huong hien nay", "trend hien nay", "dang thinh hanh", "thinh hanh",
]
# Hỏi xu hướng theo mùa / xả hàng -> đồ hot trong mùa hoặc dọn kho
SEASON_TREND_KW = ["mua nay", "he nay mac gi", "thu nay mac gi", "dong nay mac gi", "xuan nay mac gi",
                   "dau mua", "vao mua"]
# Hỏi thẳng cách xả hàng / dọn kho -> luôn trả lời bằng dữ liệu tồn kho thật, không phụ thuộc ngày demo
CLEARANCE_KW = ["xa hang", "xa kho", "thanh ly", "don kho", "giai phong hang ton",
                "ban het hang", "het hang nhanh", "chuan bi mua dong", "chuan bi mua he",
                "sap het mua", "cuoi mua", "giam gia het mua"]

POLICY_KW = ["ship", "van chuyen", "giao hang", "doi tra", "hoan tra", "bao lau", "thanh toan",
             "cod", "voucher", "ma giam", "giam gia", "khuyen mai"]
GREETING_KW = ["xin chao", "chao", "hello", "hi", "alo"]
THANKS_KW = ["cam on", "thanks", "thank you", "ok cam on"]
MIX_KW = ["phoi", "mac voi", "ket hop", "mix", "hop voi", "mac cung", "di voi"]


class AIService:
    def __init__(self):
        self._down_until: Dict[str, float] = {}  # bộ nhớ đệm "engine đang lỗi"

    # ================================================================== chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        user_msg = request.user_message.strip()
        engine = (request.engine or settings.AI_ENGINE or "auto").lower()
        if engine not in ("auto", "gemini", "ollama", "rules"):
            engine = "auto"
        context = product_service.get_by_id(request.context_product_id)

        # Xu hướng thời trang / mùa / xả hàng: trả lời dựa trên dữ liệu thật (trend_service / tồn kho)
        t_season = normalize(user_msg)
        if has_any(t_season, FASHION_TREND_KW):
            reply, ids = self._fashion_trend_reply(guess_gender(t_season))
            return ChatResponse(reply=reply, engine_used="AURA Stylist (Trend Detection)",
                                recommended_products=product_service.get_by_ids(ids)[:4],
                                quick_suggestions=DEFAULT_SUGGESTIONS)
        if has_any(t_season, CLEARANCE_KW):
            reply, ids = self._clearance_reply(guess_gender(t_season))
            return ChatResponse(reply=reply, engine_used="AURA Stylist (bộ luật nội bộ)",
                                recommended_products=product_service.get_by_ids(ids)[:4],
                                quick_suggestions=DEFAULT_SUGGESTIONS)
        if has_any(t_season, SEASON_TREND_KW):
            reply, ids = self._season_trend_reply(guess_gender(t_season))
            return ChatResponse(reply=reply, engine_used="AURA Stylist (bộ luật nội bộ)",
                                recommended_products=product_service.get_by_ids(ids)[:4],
                                quick_suggestions=DEFAULT_SUGGESTIONS)

        order: List[str] = []
        if engine in ("auto", "gemini") and settings.GEMINI_API_KEY:
            order.append("gemini")
        if engine in ("auto", "ollama"):
            order.append("ollama")

        for name in order:
            if self._is_down(name):
                continue
            try:
                raw = (self._call_gemini if name == "gemini" else self._call_ollama)(
                    user_msg, request.messages, context)
                if not raw:
                    raise ValueError("empty reply")
                reply, products = self._postprocess(raw)
                label = (f"Google Gemini ({settings.GEMINI_MODEL})" if name == "gemini"
                         else f"Ollama ({settings.OLLAMA_MODEL})")
                return ChatResponse(reply=reply, engine_used=label,
                                    recommended_products=products,
                                    quick_suggestions=DEFAULT_SUGGESTIONS)
            except Exception as e:  # noqa: BLE001 - mọi lỗi mạng/LLM đều rơi về bộ luật
                self._mark_down(name)
                print(f"[AI] {name} không khả dụng, chuyển engine khác: {e}")

        reply, ids = self._rules_engine(user_msg, context)
        return ChatResponse(reply=reply, engine_used="AURA Stylist (bộ luật nội bộ)",
                            recommended_products=product_service.get_by_ids(ids)[:4],
                            quick_suggestions=DEFAULT_SUGGESTIONS)

    def _is_down(self, name: str) -> bool:
        return time.time() < self._down_until.get(name, 0)

    def _mark_down(self, name: str, seconds: int = 60):
        self._down_until[name] = time.time() + seconds

    # ------------------------------------------------------------ LLM prompt
    def _system_prompt(self, context: Optional[Product]) -> str:
        catalog = "\n".join(
            f"- {p.name} [[{p.id}]] | {money(p.final_price)} | {p.category_name} | "
            f"dịp: {', '.join(p.occasions)} | size: {', '.join(p.sizes)}"
            for p in product_service.get_all() if p.in_stock
        )
        prompt = (
            f"Bạn là stylist AI của thương hiệu thời trang {settings.APP_NAME}. "
            "Trả lời bằng tiếng Việt, thân thiện, tinh tế, ngắn gọn (tối đa khoảng 150 từ).\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. CHỈ giới thiệu sản phẩm có trong danh mục bên dưới. Tuyệt đối không bịa sản phẩm, giá, khuyến mãi.\n"
            "2. Mỗi khi nhắc một sản phẩm, viết tên ngắn gọn rồi kèm mã dạng [[prod_001]] ngay sau tên.\n"
            "3. Gợi ý 2–4 món phối hợp hài hòa theo hoàn cảnh, thời tiết, dáng người khách nói.\n"
            "4. Nếu khách hỏi ngoài chủ đề thời trang/mua sắm, lịch sự đưa câu chuyện về thời trang.\n"
            "5. Chỉ dùng **đậm** và xuống dòng; không dùng tiêu đề #, bảng.\n\n"
            f"DANH MỤC SẢN PHẨM ĐANG CÒN HÀNG:\n{catalog}\n\n"
            f"CHÍNH SÁCH: phí ship {money(settings.SHIPPING_FEE)}, miễn phí cho đơn từ "
            f"{money(settings.FREE_SHIPPING_THRESHOLD)}; mua bộ phối đồ từ AI Stylist giảm "
            f"{settings.COMBO_DISCOUNT_PERCENT}%."
        )
        if context:
            prompt += (f"\n\nKhách đang xem: {context.name} [[{context.id}]], giá "
                       f"{money(context.final_price)}, chất liệu {context.material}. "
                       "Hãy tập trung tư vấn quanh sản phẩm này.")
        return prompt

    @staticmethod
    def _history(history: List[ChatMessage], limit: int = 8) -> List[ChatMessage]:
        trimmed = history[-limit:]
        while trimmed and trimmed[0].role != "user":  # bỏ lời chào mở đầu của bot
            trimmed = trimmed[1:]
        return trimmed

    def _call_ollama(self, prompt: str, history: List[ChatMessage],
                     context: Optional[Product]) -> Optional[str]:
        messages = [{"role": "system", "content": self._system_prompt(context)}]
        messages += [{"role": m.role, "content": m.content} for m in self._history(history)]
        messages.append({"role": "user", "content": prompt})
        payload = {"model": settings.OLLAMA_MODEL, "messages": messages, "stream": False,
                   "options": {"temperature": 0.7, "num_predict": 450}}
        req = urllib.request.Request(
            f"{settings.OLLAMA_HOST}/api/chat", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=settings.OLLAMA_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data.get("message", {}).get("content") or "").strip()

    def _call_gemini(self, prompt: str, history: List[ChatMessage],
                     context: Optional[Product]) -> Optional[str]:
        contents: List[Dict] = []
        for m in self._history(history) + [ChatMessage(role="user", content=prompt)]:
            role = "user" if m.role == "user" else "model"
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"][0]["text"] += "\n" + m.content
            else:
                contents.append({"role": role, "parts": [{"text": m.content}]})
        payload = {
            "systemInstruction": {"parts": [{"text": self._system_prompt(context)}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700},
        }
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{settings.GEMINI_MODEL}:generateContent")
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY})
        with urllib.request.urlopen(req, timeout=settings.GEMINI_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()

    def _postprocess(self, raw: str) -> Tuple[str, List[Product]]:
        """Lấy mã sản phẩm LLM nhắc tới -> thẻ sản phẩm; xóa mã khỏi câu chữ hiển thị."""
        ids = ID_TOKEN_RE.findall(raw) + BARE_ID_RE.findall(raw)
        clean = ID_TOKEN_RE.sub("", raw)
        clean = BARE_ID_RE.sub("", clean)
        clean = re.sub(r"[ \t]+([,.;:!?])", r"\1", clean)
        clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
        return clean, product_service.get_by_ids(ids)[:4]

    # ------------------------------------------------------- bộ luật nội bộ
    def _rules_engine(self, text: str, context: Optional[Product]) -> Tuple[str, List[str]]:
        t = normalize(text)
        gender = guess_gender(t)

        # 1) Hỏi size + có số đo -> tính thật
        height, weight = parse_body(text)
        if height and weight:
            res = outfit_service.calculate_size(
                height, weight, gender, "regular", context.id if context else None)
            who = f" cho **{context.name}**" if context else ""
            extra = f"\n\n{res.note}" if res.note else ""
            alt = f" (số đo của bạn nằm sát ranh giới, có thể cân nhắc thêm size **{res.alternative_size}**)" if res.alternative_size else ""
            return (f"📏 Với **{height:.0f} cm / {weight:.0f} kg**, mình gợi ý size **{res.recommended_size}**"
                    f"{alt}{who}. BMI {res.bmi} ({res.bmi_category}).{extra}\n\n"
                    "Bạn muốn mình phối thêm món nào cùng không?",
                    [context.id] if context else [])
        if has_word(t, "size") or has_any(t, ["mac size", "vua khong", "chat khong"]):
            return ("📏 Để chọn size chuẩn, bạn cho mình biết **chiều cao và cân nặng** nhé "
                    "(ví dụ: *1m65, 55kg*). Hoặc bấm nút **Tính Size AI** để nhập chi tiết hơn.",
                    [context.id] if context else [])

        # 2) Ngân sách
        budget = self._parse_budget(t)
        if budget:
            picks = [p for p in product_service.get_all(sort="rating")
                     if p.final_price <= budget and p.in_stock
                     and (gender == "unisex" or p.gender in (gender, "unisex"))][:4]
            if picks:
                lines = "\n".join(f"{i}. **{short_name(p)}** — {money(p.final_price)}"
                                  for i, p in enumerate(picks, 1))
                return (f"💸 Trong tầm giá **{money(budget)}**, đây là những món được đánh giá cao:\n\n{lines}",
                        [p.id for p in picks])
            return (f"Hiện chưa có món nào dưới {money(budget)}. Bạn thử nâng ngân sách một chút, "
                    "hoặc xem mục **Flash Sale** để có giá tốt nhất nhé!", [])
        # 2b) Xu hướng thời trang (AI Trend Detection) / theo mùa / xả hàng
        if has_any(t, FASHION_TREND_KW):
            return self._fashion_trend_reply(gender)
        if has_any(t, CLEARANCE_KW):
            return self._clearance_reply(gender)
        if has_any(t, SEASON_TREND_KW):
            return self._season_trend_reply(gender)
        
        # 3) Chính sách / voucher / vận chuyển
        if has_any(t, POLICY_KW):
            vouchers = "; ".join(f"**{v.code}** ({v.discount_display})" for v in product_service.get_vouchers())
            return (f"🚚 **Vận chuyển:** phí {money(settings.SHIPPING_FEE)}, **miễn phí cho đơn từ "
                    f"{money(settings.FREE_SHIPPING_THRESHOLD)}**.\n"
                    f"🎟️ **Voucher đang có:** {vouchers}.\n"
                    f"✨ Mua trọn bộ do AI Stylist phối được giảm thêm **{settings.COMBO_DISCOUNT_PERCENT}%**.\n"
                    "🔄 Hỗ trợ đổi trả trong 15 ngày nếu sản phẩm lỗi hoặc không vừa.", [])

        # 4) Đang xem 1 sản phẩm cụ thể
        scenario = self._match_scenario(t)
        if context and (has_any(t, MIX_KW) or not scenario):
            return self._context_reply(context, t)

        # 5) Kịch bản theo hoàn cảnh
        if scenario:
            picks = self._scenario_picks(scenario, gender)
            lines = "\n".join(
                f"{i}. **{short_name(product_service.get_by_id(pid))}** — {why} "
                f"({money(product_service.get_by_id(pid).final_price)})"
                for i, (pid, why) in enumerate(picks, 1))
            return (f"{scenario['emoji']} **{scenario['title']}**\n\n{scenario['intro']}\n\n{lines}",
                    [pid for pid, _ in picks])

        # 5b) Tìm theo từ khóa tự do
        found = [p for p in product_service.semantic_search(text, limit=12)
                 if gender == "unisex" or p.gender in (gender, "unisex")][:4]
        if found and self._has_real_match(t, found):
            lines = "\n".join(f"{i}. **{short_name(p)}** — {money(p.final_price)}"
                              for i, p in enumerate(found, 1))
            return f"🔎 Mình tìm được vài món có thể hợp ý bạn:\n\n{lines}", [p.id for p in found]

        # 6) Xã giao
        if has_any(t, THANKS_KW):
            return "Rất vui được giúp bạn! Cần thêm gợi ý phối đồ hay chọn size cứ hỏi mình nhé 💜", []
        if has_any(t, GREETING_KW):
            return ("Xin chào bạn! Mình là **AURA Stylist** 👋 Bạn đang chuẩn bị đi đâu để mình "
                    "gợi ý trang phục phù hợp nhé?", ["prod_001", "prod_005", "prod_004"])

        return ("Mình có thể giúp bạn:\n\n"
                "✨ **Phối đồ theo hoàn cảnh** — đi làm, dự tiệc, đi biển, hẹn hò, thu đông...\n"
                "📏 **Chọn size** — nói chiều cao & cân nặng, ví dụ *1m65, 55kg*\n"
                "💸 **Gợi ý theo ngân sách** — ví dụ *áo dưới 300k*\n\n"
                "Hôm nay bạn muốn mặc đồ cho dịp nào?", [])

    # ------------------------------------------------------------- tiện ích
    @staticmethod
    def _why(p: Product) -> str:
        """Lý do gợi ý cho món lấy tự động (món chọn tay đã có lý do riêng trong SCENARIOS)."""
        material = p.material.split(",")[0].strip()
        style = p.style.split("/")[0].strip()
        return f"chất liệu {material}, phong cách {style}"

    def _scenario_picks(self, scenario: Dict, gender: str) -> List[Tuple[str, str]]:
        """Tối đa 4 món: SCENARIO_ANCHORS món kinh điển + các món khớp dịp lấy từ toàn bộ danh mục.

        Kết quả ổn định (không ngẫu nhiên): cùng câu hỏi luôn ra cùng bộ đồ.
        """
        def ok(p: Optional[Product]) -> bool:
            return bool(p) and p.in_stock and (gender == "unisex" or p.gender in (gender, "unisex"))

        curated = [(pid, why) for pid, why in scenario["picks"] if ok(product_service.get_by_id(pid))]
        picks = curated[:SCENARIO_ANCHORS]
        chosen = {pid for pid, _ in picks}

        # Theo dõi "vị trí" đã có trong bộ đồ (áo, quần, đầm/set, áo khoác, phụ kiện) để món thêm vào bổ sung
        # chứ không trùng vai trò. Đầm/set đã che cả áo lẫn quần; đủ áo + quần thì không cần thêm đầm/set.
        used_slots: Set[str] = set()

        def occupy(p: Product):
            slot = SLOT_OF.get(p.category, "accessory")
            used_slots.add(slot)
            if slot in ("dress", "set"):
                used_slots.update(("top", "bottom", "dress", "set"))
            if {"top", "bottom"} <= used_slots:
                used_slots.update(("dress", "set"))

        for pid in chosen:
            occupy(product_service.get_by_id(pid))

        occ = SCENARIO_OCCASIONS.get(scenario["key"], set())
        tags = SCENARIO_TAGS.get(scenario["key"], set())

        def relevance(p: Product) -> float:
            return 3.0 * len(occ & set(p.occasions)) + 3.0 * len(tags & set(p.tags))

        pool = [p for p in product_service.get_all(sort="popular")
                if p.id not in chosen and ok(p) and relevance(p) > 0]
        pool.sort(key=lambda p: relevance(p) + p.rating / 10 + (0.5 if p.is_hot else 0) + (0.5 if p.is_new else 0),
                  reverse=True)
        while len(picks) < 4 and pool:
            best = next((p for p in pool if SLOT_OF.get(p.category, "accessory") not in used_slots), pool[0])
            picks.append((best.id, self._why(best)))
            chosen.add(best.id)
            occupy(best)
            pool.remove(best)
        for pid, why in curated[SCENARIO_ANCHORS:]:  # thiếu món thì bù bằng các món kinh điển còn lại
            if len(picks) >= 4:
                break
            if pid not in chosen:
                picks.append((pid, why))
                chosen.add(pid)
        return picks

    @staticmethod
    def _fashion_trend_reply(gender: str) -> Tuple[str, List[str]]:
        """Gợi ý xu hướng thời trang lấy trực tiếp từ hệ thống AI Trend Detection."""
        trends = trend_service.get_trends(limit=5)
        rising = [t for t in trends if t.status == "rising"]
        top_trends = rising[:4] if rising else trends[:4]

        trending_items = trend_service.get_trending_products(limit=8, gender=gender)
        picks = [tp.product for tp in trending_items][:4]

        trend_labels = ", ".join(f"**{t.keyword}** (+{t.growth_rate}%)" for t in top_trends)
        src = trends[0].source if trends else "demo"
        src_desc = (
            "Google Trends"
            if src == "google_trends"
            else ("dữ liệu xu hướng AURA" if src == "cached" else "hệ thống theo dõi xu hướng thời trang")
        )

        lines = [
            f"{i}. **{short_name(p)}** — {money(p.final_price)} ({tp.reason.split('•')[-1].strip() if '•' in tp.reason else tp.reason})"
            for i, (p, tp) in enumerate(zip(picks, trending_items[:len(picks)]), 1)
        ]

        reply = (
            f"🔥 **Xu hướng thời trang đang nổi bật hôm nay (theo {src_desc}):**\n\n"
            f"Hiện AURA đang ghi nhận các phong cách được quan tâm và tăng trưởng mạnh như: {trend_labels}.\n\n"
            f"Dưới đây là các sản phẩm bắt trend và đang sẵn hàng trong kho dành cho bạn:\n\n"
            + "\n".join(lines)
            + "\n\nBạn muốn mình tư vấn phối đồ chi tiết hơn theo phong cách nào trên đây?"
        )
        return reply, [p.id for p in picks]

    @staticmethod
    def _season_trend_reply(gender: str) -> Tuple[str, List[str]]:
        """Giữa mùa: đồ hot nhất mùa hiện tại. Gần hết mùa: chuyển sang gợi ý xả hàng luôn,
        vì lúc này việc đẩy hàng tồn quan trọng hơn quảng bá thêm đồ cùng loại."""
        info = current_season()
        if info.is_ending_soon:
            return AIService._clearance_reply(gender, info=info)

        picks = [p for p in hot_this_season(limit=12)
                 if gender == "unisex" or p.gender in (gender, "unisex")][:4]
        if not picks:
            return (f"{info.collection['emoji']} Mùa {info.collection['label']} đang bắt đầu, "
                    "sản phẩm cho mùa này sẽ sớm được cập nhật, bạn ghé lại sau nhé!", [])
        lines = "\n".join(f"{i}. **{short_name(p)}** — đã bán {p.sold_count} · "
                          f"⭐{p.rating} ({money(p.final_price)})"
                          for i, p in enumerate(picks, 1))
        return (f"{info.collection['emoji']} **Xu hướng mùa {info.collection['label']} đang hot nhất "
                f"(còn khoảng {info.days_left} ngày nữa hết mùa):**\n\n{lines}",
                [p.id for p in picks])

    @staticmethod
    def _clearance_reply(gender: str, info=None) -> Tuple[str, List[str]]:
        """Gợi ý xả hàng: đồ đúng mùa hiện tại, tồn kho nhiều mà bán chậm, kèm mức giảm đề xuất."""
        info = info or current_season()
        picks = [p for p in clearance_candidates(limit=12)
                 if gender == "unisex" or p.gender in (gender, "unisex")][:4]
        if not picks:
            return (f"{info.collection['emoji']} Hàng mùa {info.collection['label']} hiện đang bán khá đều, "
                    "chưa có món nào tồn kho đáng lo để phải xả gấp.", [])
        lines = []
        for i, p in enumerate(picks, 1):
            sell_through = 1 - (p.stock / p.stock_total) if p.stock_total else 1
            disc = suggested_discount(p, sell_through)
            lines.append(f"{i}. **{short_name(p)}** — còn tồn {p.stock}/{p.stock_total}, "
                         f"mới bán {round(sell_through * 100)}% ⇒ đề xuất giảm thêm **{disc}%** để đẩy hàng")
        when = (f"còn khoảng {info.days_left} ngày nữa hết mùa {info.collection['label']}"
                if info.is_ending_soon else f"đang giữa mùa {info.collection['label']}")
        return (f"📦 **Gợi ý xả hàng tồn ({when}, chuẩn bị đón mùa {info.next_collection['label']}):**\n\n"
                + "\n".join(lines) +
                f"\n\n💡 Có thể đẩy nhanh bằng flash sale, mua 2 giảm thêm, hoặc mix vào set đồ combo.",
                [p.id for p in picks])
    @staticmethod
    def _match_scenario(t: str) -> Optional[Dict]:
        best, best_score = None, 0
        for sc in SCENARIOS:
            score = sum(1 for k in sc["kw"] if has_word(t, k))
            if score > best_score:
                best, best_score = sc, score
        return best

    @staticmethod
    def _parse_budget(t: str) -> Optional[int]:
        m = re.search(r"(?:duoi|toi da|khong qua|khoang|tam)\s*(\d{2,4})\s*(k|nghin|ngan|tr|trieu)?", t)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        if unit in ("tr", "trieu"):
            return n * 1_000_000
        if unit in ("k", "nghin", "ngan") or n < 2000:
            return n * 1000
        return n

    @staticmethod
    def _has_real_match(t: str, products: List[Product]) -> bool:
        toks = [x for x in t.split() if len(x) > 2]
        return any(any(tok in normalize(p.name + " " + " ".join(p.tags)) for tok in toks) for p in products)

    def _context_reply(self, p: Product, t: str) -> Tuple[str, List[str]]:
        if has_any(t, MIX_KW):
            related = product_service.get_related(p.id, limit=3)
            names = ", ".join(f"**{short_name(x)}**" for x in related)
            return (f"✨ **Phối cùng {short_name(p)}**\n\nMón này mang phong cách **{p.style}**. "
                    f"Mình gợi ý kết hợp với: {names}.\n\n"
                    "💡 Mẹo: chọn 2–3 tông màu gần nhau và sơ vin nhẹ vạt trước để tỷ lệ cơ thể đẹp hơn.",
                    [p.id] + [x.id for x in related])
        occ = ", ".join(o.replace("_", " ") for o in p.occasions[:4])
        return (f"💎 **Đánh giá về {short_name(p)}**\n\n"
                f"• **Chất liệu:** {p.material}\n"
                f"• **Phong cách:** {p.style}\n"
                f"• **Hợp với dịp:** {occ}\n"
                f"• **Giá hiện tại:** {money(p.final_price)}\n\n"
                "Bạn muốn mình tính size theo chiều cao/cân nặng, hay gợi ý món phối cùng?", [p.id])

    # ============================================================ live host
    def live_reply(self, user_name: str, comment: str) -> LiveCommentResponse:
        t = normalize(comment)
        name = user_name.strip() or "bạn"
        pinned: Optional[Product] = None
        host = "AURA Host AI"

        height, weight = parse_body(comment)
        if height and weight:
            pinned = product_service.get_by_id("prod_001")
            res = outfit_service.calculate_size(height, weight, guess_gender(t), "regular",
                                                pinned.id if pinned else None)
            reply = (f"Dạ chào {name}! {height:.0f}cm {weight:.0f}kg thì {name} mặc size {res.recommended_size} "
                     f"là vừa nha. Em ghim mẫu blazer đang được săn nhiều nhất để {name} tham khảo ạ!")
        elif has_any(t, ["blazer", "ao khoac", "khoac", "ma 01", "ma 1"]):
            pinned = product_service.get_by_id("prod_001")
            reply = (f"Dạ chào {name}! Blazer Linen đang giảm {pinned.discount_percent}%, "
                     f"chỉ {money(pinned.final_price)}, form suông Hàn Quốc rất tôn dáng ạ!")
        elif has_any(t, ["vay", "dam", "tiec", "cuoi"]):
            pinned = product_service.get_by_id("prod_004")
            reply = (f"Mẫu đầm lụa maxi đi tiệc đang được hỏi nhiều lắm! Giá {money(pinned.final_price)} "
                     f"(giảm {pinned.discount_percent}%), em ghim lên cho {name} chốt nha!")
        elif has_any(t, ["voucher", "giam gia", "ma giam", "freeship", "ship", "khuyen mai"]):
            reply = (f"Chào {name}! Shop có mã LIVE20 giảm 20% (tối đa 100K, đơn từ 199K) và mã FREESHIP. "
                     f"Đơn từ {money(settings.FREE_SHIPPING_THRESHOLD)} đã được miễn phí ship sẵn ạ!")
        elif has_any(t, ["size", "cao", "nang"]):
            reply = (f"{name} cho em xin chiều cao và cân nặng nhé, ví dụ '1m65 55kg', "
                     "em tính size chuẩn cho mình ngay ạ!")
        else:
            hot = [p for p in product_service.get_flash_sale_products() if p.in_stock]
            pinned = max(hot, key=lambda p: p.sold_count) if hot else None
            reply = (f"Cảm ơn {name} đã ghé phòng Live! Đây là món đang được chốt nhiều nhất, "
                     "cả nhà xem thử nha!") if pinned else f"Cảm ơn {name} đã ghé phòng Live của AURA STUDIO!"
        return LiveCommentResponse(host_name=host, reply=reply, pinned_product=pinned)


ai_service = AIService()