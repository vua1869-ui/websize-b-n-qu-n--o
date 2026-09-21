from typing import List, Optional, Set

from app.config import settings
from app.models.schemas import OutfitResponse, Product, SizeRecommendResponse
from app.services.order_service import make_combo_token
from app.services.product_service import product_service
from app.services.text_utils import tokens

# Vai trò của từng loại sản phẩm trong một bộ đồ
SLOT_OF = {
    "ao_thun": "top", "ao_so_mi": "top",
    "quan_tay": "bottom", "quan_jeans": "bottom", "chan_vay": "bottom",
    "vay_dam": "dress", "set_do": "set",
    "ao_khoac": "outer", "phu_kien": "accessory",
}

# Công thức: base thuộc slot nào thì cần thêm các slot nào (theo thứ tự ưu tiên)
RECIPES = {
    "top": ["bottom", "outer", "accessory"],
    "bottom": ["top", "outer", "accessory"],
    "dress": ["outer", "accessory", "accessory"],
    "set": ["accessory", "accessory"],
    "outer": ["top", "bottom", "accessory"],
    "accessory": ["top", "bottom", "outer"],
}

OCCASION_LOOKS = {
    "du_tiec": ("Set Dạ Tiệc Quý Phái", "Chất liệu rủ mềm cùng phụ kiện tối giản tạo vẻ sang trọng mà không phô trương.",
                "Chọn tối đa 1 điểm nhấn (túi hoặc kính); giữ các món còn lại tông trầm để tổng thể thanh lịch."),
    "cong_so": ("Set Công Sở Thanh Lịch", "Form suông gọn gàng, tông màu trung tính giúp bạn chuyên nghiệp mà vẫn thoải mái cả ngày.",
                "Sơ vin nhẹ vạt trước để kéo dài chân; ưu tiên tông be, xám, đen, xanh navy."),
    "di_bien": ("Set Nghỉ Dưỡng Biển", "Chất liệu thoáng nhẹ, màu tươi sáng, đi biển hay dạo phố đều hợp.",
                "Kết hợp một món họa tiết với một món trơn để không bị rối mắt; đừng quên kính mát."),
    "hen_ho": ("Set Hẹn Hò Nhẹ Nhàng", "Dịu dàng, gọn gàng và dễ chịu — vừa đủ chỉn chu cho buổi cà phê cuối tuần.",
               "Giữ bảng màu 2–3 tông gần nhau; phụ kiện nhỏ gọn sẽ ăn điểm hơn bộ cánh cầu kỳ."),
    "thu_dong": ("Set Thu Đông Ấm Áp", "Phối nhiều lớp mỏng nhẹ để giữ ấm mà không bị nặng nề.",
                 "Lớp trong ôm vừa, lớp ngoài rộng hơn một chút; đổi tông màu giữa các lớp để có chiều sâu."),
}
DEFAULT_LOOK = ("Set Smart Casual Hiện Đại", "Sự kết hợp cân bằng giữa thoải mái và chỉn chu, mặc được nhiều dịp.",
                "Áp dụng quy tắc 3 màu: một màu chủ đạo, một màu phụ, một màu nhấn. Sơ vin vạt trước để tôn dáng.")

LETTERS = ["S", "M", "L", "XL", "XXL"]
# Ngưỡng cân nặng (kg) tối đa của S, M, L, XL; trên ngưỡng cuối là XXL
WEIGHT_CUTS = {"nam": [58, 66, 74, 82], "nu": [48, 54, 60, 66], "unisex": [53, 60, 68, 76]}
TALL_CM = {"nam": 178, "nu": 168, "unisex": 173}


def _compatible(p: Product, gender: Optional[str]) -> bool:
    if not gender or gender in ("all", "unisex") or p.gender == "unisex":
        return True
    return p.gender == gender


class OutfitService:
    # ---------------------------------------------------------------- phối đồ
    def get_outfit(self, product_id: Optional[str] = None, occasion: Optional[str] = None,
                   gender: Optional[str] = None, variant: int = 0) -> OutfitResponse:
        base = product_service.get_by_id(product_id)
        pool = [p for p in product_service.get_all() if p.in_stock]

        if base and base.gender in ("nam", "nu"):
            gender = base.gender
        target_occ: Set[str] = set(base.occasions) if base else set()
        if occasion:
            target_occ.add(occasion)
        style_tokens = set(tokens(base.style)) if base else set()

        def score(p: Product) -> float:
            s = 3.0 * len(target_occ & set(p.occasions))
            s += 2.0 * len(style_tokens & set(tokens(p.style)))
            s += p.rating / 10 + (0.5 if p.is_hot else 0)
            return s

        items: List[Product] = []
        if base:
            items.append(base)
            slots = RECIPES.get(SLOT_OF.get(base.category, "accessory"), ["top", "bottom"])
        else:
            female = gender == "nu"
            slots = ["dress", "accessory", "outer"] if female and occasion in ("du_tiec", "hen_ho", "di_bien") \
                else ["top", "bottom", "accessory"]

        chosen_slots = {SLOT_OF.get(base.category)} if base else set()
        for slot in slots:
            candidates = [
                p for p in pool
                if SLOT_OF.get(p.category) == slot
                and p.id not in {x.id for x in items}
                and _compatible(p, gender)
                # chỉ phụ kiện được phép lặp trong một bộ đồ
                and (slot == "accessory" or slot not in chosen_slots)
            ]
            if not candidates:
                continue
            candidates.sort(key=score, reverse=True)
            pick = candidates[(variant + len(items)) % min(3, len(candidates)) if variant else 0]
            items.append(pick)
            chosen_slots.add(slot)

        if not items:  # kho trống bất thường
            items = pool[:3]

        total = sum(p.final_price for p in items)
        pct = settings.COMBO_DISCOUNT_PERCENT
        combo_price = total - total * pct // 100

        look_key = occasion if occasion in OCCASION_LOOKS else next(
            (o for o in (base.occasions if base else []) if o in OCCASION_LOOKS), None)
        name, concept, tip = OCCASION_LOOKS.get(look_key, DEFAULT_LOOK)

        return OutfitResponse(
            outfit_name=name, style_concept=concept, style_tip=tip, items=items,
            total_price=total, discounted_combo_price=combo_price, discount_percentage=pct,
            combo_token=make_combo_token([p.id for p in items]),
        )

    # -------------------------------------------------------------- tính size
    def calculate_size(self, height_cm: float, weight_kg: float, gender: str = "unisex",
                       fit_preference: str = "regular",
                       product_id: Optional[str] = None) -> SizeRecommendResponse:
        gender = gender if gender in WEIGHT_CUTS else "unisex"
        bmi = round(weight_kg / ((height_cm / 100) ** 2), 1)
        if bmi < 18.5:
            bmi_category = "Thon gọn / Gầy"
        elif bmi < 23:
            bmi_category = "Cân đối"
        elif bmi < 25:
            bmi_category = "Hơi đậm người"
        elif bmi < 30:
            bmi_category = "Đậm người"
        else:
            bmi_category = "Ngoại cỡ"

        cuts = WEIGHT_CUTS[gender]
        idx = sum(1 for c in cuts if weight_kg > c)  # 0..4
        if height_cm >= TALL_CM[gender]:
            idx += 1

        # Gợi ý size liền kề nếu cân nặng nằm sát ranh giới hai size
        alt_idx: Optional[int] = None
        for i, c in enumerate(cuts):
            if abs(weight_kg - c) <= 2:
                alt_idx = i + 1 if weight_kg <= c else i
                break

        notes: List[str] = []
        if fit_preference == "oversize":
            idx += 1
        elif fit_preference == "slim":
            if bmi < 23:
                idx -= 1
            else:
                notes.append("Với form ôm, chúng tôi vẫn giữ size theo số đo để bạn thoải mái.")
        idx = max(0, min(idx, len(LETTERS) - 1))
        letter = LETTERS[idx]
        alt_letter = LETTERS[max(0, min(alt_idx, 4))] if alt_idx is not None else None
        if alt_letter == letter:
            alt_letter = None

        # Số đo ước tính (tham khảo)
        if gender == "nu":
            chest, waist = 80 + (weight_kg - 48) * 0.9, 62 + (weight_kg - 48) * 0.8
            chest_label = "Vòng ngực"
        else:
            chest, waist = 84 + (weight_kg - 55) * 0.8, 70 + (weight_kg - 55) * 0.8
            chest_label = "Vòng ngực"
        chest, waist = int(round(chest)), int(round(waist))
        length = int(round(height_cm * 0.42))

        recommended, alternative, available = letter, alt_letter, []
        product = product_service.get_by_id(product_id)
        if product:
            recommended, alternative, available, extra = self._map_to_product(
                product, idx, alt_letter, waist, fit_preference)
            notes.extend(extra)

        fit_word = {"slim": "ôm vừa người", "regular": "vừa vặn", "oversize": "rộng rãi thoải mái"}[fit_preference]
        advice = (f"Với chiều cao {height_cm:.0f} cm, cân nặng {weight_kg:.0f} kg (BMI {bmi} - {bmi_category}), "
                  f"size {recommended} sẽ cho cảm giác {fit_word}. Số đo bên dưới chỉ là ước tính; "
                  f"nếu có thước dây, hãy đối chiếu với bảng size của từng sản phẩm.")

        return SizeRecommendResponse(
            recommended_size=recommended, alternative_size=alternative,
            available_sizes=available, bmi=bmi, bmi_category=bmi_category, fit_advice=advice,
            measurements_estimated={
                f"{chest_label} ước tính": f"{chest} - {chest + 4} cm",
                "Vòng eo ước tính": f"{waist} - {waist + 4} cm",
                "Chiều dài áo phù hợp": f"~{length} cm",
            },
            note=" ".join(notes) or None,
        )

    def _map_to_product(self, p: Product, idx: int, alt_letter: Optional[str], waist_cm: int,
                        fit: str):
        """Quy đổi size chữ chuẩn sang size thật mà sản phẩm đang bán."""
        sizes = p.sizes
        notes: List[str] = []
        lowered = [s.lower() for s in sizes]

        # 1) Freesize
        if len(sizes) == 1 and lowered[0].startswith("free"):
            return sizes[0], None, sizes, ["Sản phẩm này là freesize, không cần chọn size."]
        if len(sizes) == 1:
            return sizes[0], None, sizes, ["Sản phẩm này chỉ có một lựa chọn kích cỡ."]

        # 2) Size chữ (S/M/L...)
        letter_sizes = [s for s in sizes if s.upper() in LETTERS]
        if letter_sizes:
            avail_idx = sorted(LETTERS.index(s.upper()) for s in letter_sizes)
            best = min(avail_idx, key=lambda i: (abs(i - idx), -i if fit != "slim" else i))
            if idx > avail_idx[-1]:
                notes.append(f"Sản phẩm này chỉ có tới size {LETTERS[avail_idx[-1]]}, "
                             f"có thể hơi chật với bạn.")
            elif idx < avail_idx[0]:
                notes.append(f"Sản phẩm này nhỏ nhất là size {LETTERS[avail_idx[0]]}, "
                             f"có thể hơi rộng với bạn.")
            chosen = next(s for s in letter_sizes if s.upper() == LETTERS[best])
            alt = None
            if alt_letter:
                alt_best = min(avail_idx, key=lambda i: abs(i - LETTERS.index(alt_letter)))
                alt_size = next(s for s in letter_sizes if s.upper() == LETTERS[alt_best])
                alt = alt_size if alt_size != chosen else None
            return chosen, alt, sizes, notes

        # 3) Size số đo eo (28, 29, 30... tính bằng inch, như quần jeans)
        numeric = []
        for s in sizes:
            try:
                numeric.append((float(s), s))
            except ValueError:
                pass
        if numeric:
            inch = waist_cm / 2.54 + (0.5 if fit == "oversize" else 0) - (0.5 if fit == "slim" else 0)
            value, label = min(numeric, key=lambda t: (abs(t[0] - inch), -t[0]))
            notes.append(f"Size quần tính theo vòng eo (inch). Eo ước tính của bạn ≈ {inch:.0f} inch.")
            return label, None, sizes, notes

        # 4) Không nhận dạng được
        return LETTERS[idx], None, sizes, ["Hãy đối chiếu với bảng size của sản phẩm."]


outfit_service = OutfitService()
