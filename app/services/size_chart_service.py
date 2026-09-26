"""Dịch vụ cung cấp thông số bảng size chi tiết (Size Chart) và hướng dẫn đo chuẩn AURA Studio."""
from typing import Any, Dict, List, Optional
from app.services.product_service import product_service


class SizeChartService:
    @staticmethod
    def get_measuring_guide() -> List[Dict[str, str]]:
        return [
            {
                "part": "Vòng ngực (Bust)",
                "how_to": "Vòng thước dây qua phần nhô cao nhất của ngực, giữ thước thẳng ngang lưng và thả lỏng ngực tự nhiên.",
                "tip": "Không nên siết quá chặt, cộng thêm 1-2cm nếu thích mặc thoải mái."
            },
            {
                "part": "Vòng eo (Waist)",
                "how_to": "Đo quanh phần thon nhỏ nhất của thắt lưng (thường cách rốn khoảng 2-3cm lên phía trên).",
                "tip": "Đo lúc bụng ở trạng thái bình thường sau khi thở nhẹ ra."
            },
            {
                "part": "Vòng mông (Hips)",
                "how_to": "Khép hai chân lại, vòng thước dây qua điểm nở nang nhất của vòng ba.",
                "tip": "Đảm bảo thước song song với mặt sàn khi đo."
            },
            {
                "part": "Rộng vai & Dài áo",
                "how_to": "Rộng vai đo từ mút xương vai trái sang vai phải. Dài áo đo từ chân cổ áo thẳng xuống vạt áo.",
                "tip": "Nên nhờ người thân đo hộ hoặc đo trên một chiếc áo bạn mặc vừa vặn nhất."
            }
        ]

    @staticmethod
    def get_care_instructions(category: str) -> List[str]:
        base = [
            "Giặt ở nhiệt độ thường, tối đa 30°C với các sản phẩm cùng tông màu.",
            "Không sử dụng thuốc tẩy có chứa clo để bảo vệ sợi vải và màu nhuộm sinh học.",
            "Phơi nơi râm mát thoáng gió, tránh ánh nắng mặt trời chiếu trực tiếp.",
            "Ủi ở nhiệt độ trung bình hoặc sử dụng bàn ủi hơi nước để giữ form dáng chuẩn nhất."
        ]
        if "vay" in category.lower() or "dam" in category.lower() or "lua" in category.lower():
            base.insert(0, "Khuyến khích giặt tay hoặc dùng túi giặt chế độ nhẹ (Delicate) cho chất liệu lụa satin / tơ tằm.")
        return base

    def get_chart_for_product(self, product_id: str) -> Dict[str, Any]:
        p = product_service.get_by_id(product_id)
        if not p:
            cat_id = "ao"
            cat_name = "Thời trang"
            p_name = "Sản phẩm AURA"
        else:
            cat_id = p.category.lower()
            cat_name = p.category_name or "Thời trang AURA"
            p_name = p.name

        # 1. Phân loại theo nhóm sản phẩm: Quần, Váy/Đầm, hoặc Áo
        if any(k in cat_id for k in ["quan", "jeans", "pants", "short"]):
            columns = ["Size", "Vòng eo (cm)", "Vòng mông (cm)", "Dài quần (cm)", "Rộng đùi (cm)", "Rộng gấu (cm)", "Cân nặng phù hợp"]
            rows = [
                {"size": "26 / S", "specs": {"Vòng eo (cm)": "66 - 69", "Vòng mông (cm)": "88 - 91", "Dài quần (cm)": "98", "Rộng đùi (cm)": "52", "Rộng gấu (cm)": "20.5", "Cân nặng phù hợp": "42 - 48 kg"}},
                {"size": "27-28 / M", "specs": {"Vòng eo (cm)": "70 - 74", "Vòng mông (cm)": "92 - 96", "Dài quần (cm)": "100", "Rộng đùi (cm)": "54", "Rộng gấu (cm)": "21.5", "Cân nặng phù hợp": "48 - 56 kg"}},
                {"size": "29-30 / L", "specs": {"Vòng eo (cm)": "75 - 79", "Vòng mông (cm)": "97 - 101", "Dài quần (cm)": "102", "Rộng đùi (cm)": "57", "Rộng gấu (cm)": "22.5", "Cân nặng phù hợp": "56 - 65 kg"}},
                {"size": "31-32 / XL", "specs": {"Vòng eo (cm)": "80 - 85", "Vòng mông (cm)": "102 - 106", "Dài quần (cm)": "104", "Rộng đùi (cm)": "60", "Rộng gấu (cm)": "23.5", "Cân nặng phù hợp": "65 - 75 kg"}},
                {"size": "33-34 / XXL", "specs": {"Vòng eo (cm)": "86 - 92", "Vòng mông (cm)": "107 - 112", "Dài quần (cm)": "106", "Rộng đùi (cm)": "63", "Rộng gấu (cm)": "24.5", "Cân nặng phù hợp": "75 - 88 kg"}},
            ]
        elif any(k in cat_id for k in ["vay", "dam", "dress", "skirt"]):
            columns = ["Size", "Vòng 1 (cm)", "Vòng 2 (cm)", "Vòng 3 (cm)", "Dài váy (cm)", "Chiều cao đề xuất", "Cân nặng phù hợp"]
            rows = [
                {"size": "XS", "specs": {"Vòng 1 (cm)": "78 - 82", "Vòng 2 (cm)": "60 - 64", "Vòng 3 (cm)": "84 - 88", "Dài váy (cm)": "110", "Chiều cao đề xuất": "1m50 - 1m58", "Cân nặng phù hợp": "40 - 46 kg"}},
                {"size": "S", "specs": {"Vòng 1 (cm)": "82 - 86", "Vòng 2 (cm)": "64 - 68", "Vòng 3 (cm)": "88 - 92", "Dài váy (cm)": "112", "Chiều cao đề xuất": "1m55 - 1m62", "Cân nặng phù hợp": "46 - 52 kg"}},
                {"size": "M", "specs": {"Vòng 1 (cm)": "86 - 90", "Vòng 2 (cm)": "68 - 72", "Vòng 3 (cm)": "92 - 96", "Dài váy (cm)": "114", "Chiều cao đề xuất": "1m58 - 1m68", "Cân nặng phù hợp": "51 - 57 kg"}},
                {"size": "L", "specs": {"Vòng 1 (cm)": "90 - 95", "Vòng 2 (cm)": "72 - 77", "Vòng 3 (cm)": "96 - 101", "Dài váy (cm)": "116", "Chiều cao đề xuất": "1m62 - 1m72", "Cân nặng phù hợp": "57 - 65 kg"}},
                {"size": "XL", "specs": {"Vòng 1 (cm)": "95 - 100", "Vòng 2 (cm)": "78 - 83", "Vòng 3 (cm)": "101 - 106", "Dài váy (cm)": "118", "Chiều cao đề xuất": "1m65 - 1m75", "Cân nặng phù hợp": "64 - 72 kg"}},
            ]
        else:
            # Mặc định: Áo sơ mi, Blazer, Áo thun, Polo, Outerwear
            columns = ["Size", "Rộng vai (cm)", "Vòng ngực (cm)", "Dài áo (cm)", "Dài tay (cm)", "Chiều cao đề xuất", "Cân nặng phù hợp"]
            rows = [
                {"size": "S", "specs": {"Rộng vai (cm)": "43 - 44", "Vòng ngực (cm)": "96 - 98", "Dài áo (cm)": "68", "Dài tay (cm)": "21 / 58", "Chiều cao đề xuất": "1m55 - 1m65", "Cân nặng phù hợp": "45 - 54 kg"}},
                {"size": "M", "specs": {"Rộng vai (cm)": "45 - 46", "Vòng ngực (cm)": "100 - 104", "Dài áo (cm)": "70", "Dài tay (cm)": "22 / 60", "Chiều cao đề xuất": "1m62 - 1m72", "Cân nặng phù hợp": "53 - 62 kg"}},
                {"size": "L", "specs": {"Rộng vai (cm)": "47 - 48", "Vòng ngực (cm)": "106 - 110", "Dài áo (cm)": "72", "Dài tay (cm)": "23 / 62", "Chiều cao đề xuất": "1m68 - 1m78", "Cân nặng phù hợp": "62 - 72 kg"}},
                {"size": "XL", "specs": {"Rộng vai (cm)": "49 - 51", "Vòng ngực (cm)": "112 - 116", "Dài áo (cm)": "74", "Dài tay (cm)": "24 / 63", "Chiều cao đề xuất": "1m73 - 1m85", "Cân nặng phù hợp": "71 - 82 kg"}},
                {"size": "XXL", "specs": {"Rộng vai (cm)": "52 - 54", "Vòng ngực (cm)": "118 - 122", "Dài áo (cm)": "76", "Dài tay (cm)": "25 / 64", "Chiều cao đề xuất": "1m75 - 1m90", "Cân nặng phù hợp": "80 - 92 kg"}},
            ]

        return {
            "product_id": product_id,
            "product_name": p_name,
            "category_name": cat_name,
            "unit": "cm",
            "columns": columns,
            "rows": rows,
            "measuring_guide": self.get_measuring_guide(),
            "care_instructions": self.get_care_instructions(cat_id)
        }


size_chart_service = SizeChartService()
