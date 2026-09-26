# app/services/geo_service.py
"""Dịch vụ cung cấp danh mục địa giới hành chính 3 cấp của Việt Nam:
Tỉnh/Thành phố -> Quận/Huyện -> Phường/Xã chuẩn hóa.
"""
from typing import Any, Dict, List, Optional, Tuple

# Danh mục 63 Tỉnh/Thành phố và các Quận/Huyện, Phường/Xã tiêu biểu
VIETNAM_GEO_DATA: Dict[str, Dict[str, List[str]]] = {
    "Hà Nội": {
        "Quận Ba Đình": ["Phường Cống Vị", "Phường Điện Biên", "Phường Đội Cấn", "Phường Giảng Võ", "Phường Kim Mã", "Phường Liễu Giai", "Phường Quán Thánh", "Phường Thành Công"],
        "Quận Hoàn Kiếm": ["Phường Cửa Đông", "Phường Cửa Nam", "Phường Đồng Xuân", "Phường Hàng Bạc", "Phường Hàng Bài", "Phường Hàng Bông", "Phường Hàng Gai", "Phường Tràng Tiền"],
        "Quận Cầu Giấy": ["Phường Dịch Vọng", "Phường Dịch Vọng Hậu", "Phường Mai Dịch", "Phường Nghĩa Đô", "Phường Nghĩa Tân", "Phường Quan Hoa", "Phường Trung Hòa", "Phường Yên Hòa"],
        "Quận Đống Đa": ["Phường Cát Linh", "Phường Hàng Bột", "Phường Khâm Thiên", "Phường Láng Hạ", "Phường Láng Thượng", "Phường Ô Chợ Dừa", "Phường Quang Trung", "Phường Văn Miếu"],
        "Quận Hai Bà Trưng": ["Phường Bách Khoa", "Phường Bạch Đằng", "Phường Cầu Dền", "Phường Đồng Tâm", "Phường Lê Đại Hành", "Phường Minh Khai", "Phường Phố Huế", "Phường Vĩnh Tuy"],
        "Quận Tây Hồ": ["Phường Bưởi", "Phường Nhật Tân", "Phường Quảng An", "Phường Thụy Khuê", "Phường Tứ Liên", "Phường Xuân La", "Phường Yên Phụ"],
        "Quận Thanh Xuân": ["Phường Hạ Đình", "Phường Khương Đình", "Phường Khương Mai", "Phường Khương Trung", "Phường Nhân Chính", "Phường Phương Liệt", "Phường Thanh Xuân Bắc"],
        "Quận Nam Từ Liêm": ["Phường Cầu Diễn", "Phường Mỹ Đình 1", "Phường Mỹ Đình 2", "Phường Mễ Trì", "Phường Phú Đô", "Phường Trung Văn"],
        "Quận Bắc Từ Liêm": ["Phường Cổ Nhuế 1", "Phường Cổ Nhuế 2", "Phường Đức Thắng", "Phường Minh Khai", "Phường Phú Diễn", "Phường Xuân Đỉnh"],
        "Quận Hà Đông": ["Phường Quang Trung", "Phường Yết Kiêu", "Phường Hà Cầu", "Phường Vạn Phúc", "Phường Mộ Lao", "Phường Văn Quán", "Phường La Khê"],
        "Quận Long Biên": ["Phường Bồ Đề", "Phường Gia Thụy", "Phường Ngọc Lâm", "Phường Ngọc Thụy", "Phường Phúc Đồng", "Phường Sài Đồng"],
        "Quận Hoàng Mai": ["Phường Đại Kim", "Phường Định Công", "Phường Giáp Bát", "Phường Hoàng Liệt", "Phường Mai Động", "Phường Tân Mai", "Phường Vĩnh Hưng"],
    },
    "TP. Hồ Chí Minh": {
        "Quận 1": ["Phường Bến Nghé", "Phường Bến Thành", "Phường Cầu Kho", "Phường Cầu Ông Lãnh", "Phường Cô Giang", "Phường Đa Kao", "Phường Nguyễn Cư Trinh", "Phường Nguyễn Thái Bình", "Phường Phạm Ngũ Lão", "Phường Tân Định"],
        "Quận 3": ["Phường 1", "Phường 2", "Phường 3", "Phường 4", "Phường 5", "Phường 9", "Phường 11", "Phường 12", "Phường 14", "Phường Võ Thị Sáu"],
        "Quận 4": ["Phường 1", "Phường 2", "Phường 3", "Phường 4", "Phường 6", "Phường 8", "Phường 9", "Phường 13", "Phường 15", "Phường 16", "Phường 18"],
        "Quận 5": ["Phường 1", "Phường 2", "Phường 3", "Phường 4", "Phường 5", "Phường 6", "Phường 7", "Phường 8", "Phường 9", "Phường 10", "Phường 11", "Phường 12", "Phường 13", "Phường 14"],
        "Quận 7": ["Phường Tân Thuận Đông", "Phường Tân Thuận Tây", "Phường Tân Kiểng", "Phường Tân Hưng", "Phường Bình Thuận", "Phường Tân Quy", "Phường Phú Thuận", "Phường Tân Phú", "Phường Tân Phong", "Phường Phú Mỹ"],
        "Quận 10": ["Phường 1", "Phường 2", "Phường 4", "Phường 6", "Phường 8", "Phường 9", "Phường 10", "Phường 12", "Phường 13", "Phường 14", "Phường 15"],
        "Quận Bình Thạnh": ["Phường 1", "Phường 2", "Phường 3", "Phường 5", "Phường 6", "Phường 7", "Phường 11", "Phường 12", "Phường 13", "Phường 14", "Phường 15", "Phường 17", "Phường 19", "Phường 21", "Phường 22", "Phường 25", "Phường 26"],
        "Quận Phú Nhuận": ["Phường 1", "Phường 2", "Phường 3", "Phường 4", "Phường 5", "Phường 7", "Phường 8", "Phường 9", "Phường 10", "Phường 11", "Phường 13", "Phường 15", "Phường 17"],
        "Quận Tân Bình": ["Phường 1", "Phường 2", "Phường 3", "Phường 4", "Phường 5", "Phường 6", "Phường 7", "Phường 8", "Phường 9", "Phường 10", "Phường 11", "Phường 12", "Phường 13", "Phường 14", "Phường 15"],
        "TP. Thủ Đức": ["Phường An Khánh", "Phường An Lợi Đông", "Phường An Phú", "Phường Bình Chiểu", "Phường Bình Thọ", "Phường Hiệp Bình Chánh", "Phường Hiệp Bình Phước", "Phường Linh Chiểu", "Phường Linh Đông", "Phường Linh Trung", "Phường Thảo Điền", "Phường Thủ Thiêm"],
        "Quận Gò Vấp": ["Phường 1", "Phường 3", "Phường 5", "Phường 6", "Phường 7", "Phường 8", "Phường 9", "Phường 10", "Phường 11", "Phường 12", "Phường 14", "Phường 16"],
        "Quận Tân Phú": ["Phường Hiệp Tân", "Phường Hòa Thạnh", "Phường Phú Thạnh", "Phường Phú Thọ Hòa", "Phường Phú Trung", "Phường Sơn Kỳ", "Phường Tân Quý", "Phường Tân Sơn Nhì", "Phường Tân Thành", "Phường Tây Thạnh"],
    },
    "Đà Nẵng": {
        "Quận Hải Châu": ["Phường Hải Châu 1", "Phường Hải Châu 2", "Phường Thạch Thang", "Phường Thanh Bình", "Phường Thuận Phước", "Phường Hòa Thuận Đông", "Phường Hòa Thuận Tây", "Phường Nam Dương", "Phường Phước Ninh", "Phường Bình Thuận"],
        "Quận Thanh Khê": ["Phường Tam Thuận", "Phường Thanh Khê Tây", "Phường Thanh Khê Đông", "Phường Xuân Hà", "Phường Tân Chính", "Phường Chính Gián", "Phường Vĩnh Trung", "Phường Thạc Gián", "Phường An Khê", "Phường Hòa Khê"],
        "Quận Sơn Trà": ["Phường An Hải Bắc", "Phường An Hải Tây", "Phường An Hải Đông", "Phường Phước Mỹ", "Phường Mân Thái", "Phường Thọ Quang", "Phường Nại Hiên Đông"],
        "Quận Ngũ Hành Sơn": ["Phường Mỹ An", "Phường Khuê Mỹ", "Phường Hòa Quý", "Phường Hòa Hải"],
        "Quận Cẩm Lệ": ["Phường Khuê Trung", "Phường Hòa Phát", "Phường Hòa An", "Phường Hòa Thọ Tây", "Phường Hòa Thọ Đông", "Phường Hòa Xuân"],
        "Quận Liên Chiểu": ["Phường Hòa Hiệp Bắc", "Phường Hòa Hiệp Nam", "Phường Hòa Khánh Bắc", "Phường Hòa Khánh Nam", "Phường Hòa Minh"],
    },
    "Hải Phòng": {
        "Quận Hồng Bàng": ["Phường Hạ Lý", "Phường Hoàng Văn Thụ", "Phường Hùng Vương", "Phường Minh Khai", "Phường Phan Bội Châu", "Phường Quán Toan", "Phường Sở Dầu", "Phường Thượng Lý", "Phường Trại Chuối"],
        "Quận Ngô Quyền": ["Phường Cầu Đất", "Phường Cầu Tre", "Phường Đằng Giang", "Phường Đông Khê", "Phường Đồng Quốc Bình", "Phường Gia Viên", "Phường Lạc Viên", "Phường Lạch Tray", "Phường Lê Lợi", "Phường Máy Chai", "Phường Máy Tơ", "Phường Vạn Mỹ"],
        "Quận Lê Chân": ["Phường An Biên", "Phường An Dương", "Phường Cát Dài", "Phường Dư Hàng", "Phường Dư Hàng Kênh", "Phường Hàng Kênh", "Phường Hồ Nam", "Phường Kênh Dương", "Phường Lam Sơn", "Phường Niệm Nghĩa", "Phường Nghĩa Xá", "Phường Trại Cau", "Phường Trần Nguyên Hãn", "Phường Vĩnh Niệm"],
    },
    "Cần Thơ": {
        "Quận Ninh Kiều": ["Phường An Bình", "Phường An Cư", "Phường An Hòa", "Phường An Khánh", "Phường An Nghiệp", "Phường Cái Khế", "Phường Hưng Lợi", "Phường Tân An", "Phường Thới Bình", "Phường Xuân Khánh"],
        "Quận Bình Thủy": ["Phường An Thới", "Phường Bình Thủy", "Phường Bùi Hữu Nghĩa", "Phường Long Hòa", "Phường Long Tuyền", "Phường Thới An Đông", "Phường Trà An", "Phường Trà Nóc"],
        "Quận Cái Răng": ["Phường Ba Láng", "Phường Hưng Phú", "Phường Hưng Thạnh", "Phường Lê Bình", "Phường Phú Thứ", "Phường Tân Phú"],
    },
    "Bình Dương": {
        "TP. Thủ Dầu Một": ["Phường Chánh Mỹ", "Phường Chánh Nghĩa", "Phường Định Hòa", "Phường Hiệp An", "Phường Hiệp Thành", "Phường Hòa Phú", "Phường Phú Cường", "Phường Phú Hòa", "Phường Phú Lợi", "Phường Phú Mỹ", "Phường Phú Tân", "Phường Phú Thọ", "Phường Tân An", "Phường Tương Bình Hiệp"],
        "TP. Dĩ An": ["Phường An Bình", "Phường Bình An", "Phường Bình Thắng", "Phường Dĩ An", "Phường Đông Hòa", "Phường Tân Bình", "Phường Tân Đông Hiệp"],
        "TP. Thuận An": ["Phường An Phú", "Phường An Thạnh", "Phường Bình Chuẩn", "Phường Bình Hòa", "Phường Bình Nhâm", "Phường Hưng Định", "Phường Lái Thiêu", "Phường Thuận Giao", "Phường Vĩnh Phú"],
    },
    "Đồng Nai": {
        "TP. Biên Hòa": ["Phường An Bình", "Phường An Hòa", "Phường Bình Đa", "Phường Bửu Hòa", "Phường Bửu Long", "Phường Hiệp Hòa", "Phường Hóa An", "Phường Hố Nai", "Phường Long Bình", "Phường Long Bình Tân", "Phường Quang Vinh", "Phường Quyết Thắng", "Phường Tam Hiệp", "Phường Tam Hòa", "Phường Tân Biên", "Phường Tân Hạnh", "Phường Tân Hiệp", "Phường Tân Hòa", "Phường Tân Mai", "Phường Tân Phong", "Phường Tân Tiến", "Phường Tân Vạn", "Phường Thanh Bình", "Phường Thống Nhất", "Phường Trảng Dài", "Phường Trung Dũng"],
        "TP. Long Khánh": ["Phường Phú Bình", "Phường Suối Tre", "Phường Xuân An", "Phường Xuân Bình", "Phường Xuân Hòa", "Phường Xuân Trung"],
    },
    "Quảng Ninh": {
        "TP. Hạ Long": ["Phường Bãi Cháy", "Phường Bạch Đằng", "Phường Cao Thắng", "Phường Cao Xanh", "Phường Đại Yên", "Phường Giếng Đáy", "Phường Hà Khánh", "Phường Hà Khẩu", "Phường Hà Lầm", "Phường Hà Phong", "Phường Hà Trung", "Phường Hà Tu", "Phường Hoành Bồ", "Phường Hùng Thắng", "Phường Hồng Gai", "Phường Hồng Hà", "Phường Hồng Hải", "Phường Trần Hưng Đạo", "Phường Tuần Châu", "Phường Việt Hưng", "Phường Yết Kiêu"],
    },
    "Bắc Ninh": {
        "TP. Bắc Ninh": ["Phường Đại Phúc", "Phường Đáp Cầu", "Phường Hạp Lĩnh", "Phường Khắc Niệm", "Phường Khúc Xuyên", "Phường Kim Chân", "Phường Kinh Bắc", "Phường Nam Sơn", "Phường Ninh Xá", "Phường Phong Khê", "Phường Suối Hoa", "Phường Tiền An", "Phường Thị Cầu", "Phường Vạn An", "Phường Vân Dương", "Phường Vệ An", "Phường Võ Cường", "Phường Vũ Ninh"],
    },
    "Thừa Thiên Huế": {
        "TP. Huế": ["Phường An Cựu", "Phường An Đông", "Phường An Hòa", "Phường An Tây", "Phường Đông Ba", "Phường Gia Hội", "Phường Hương An", "Phường Hương Hồ", "Phường Hương Long", "Phường Hương Sơ", "Phường Hương Vinh", "Phường Kim Long", "Phường Phú Hội", "Phường Phú Hậu", "Phường Phú Nhuận", "Phường Phú Thượng", "Phường Phước Vĩnh", "Phường Phường Đúc", "Phường Tây Lộc", "Phường Thuận An", "Phường Thuận Hòa", "Phường Thuận Lộc", "Phường Thủy Biều", "Phường Thủy Vân", "Phường Thủy Xuân", "Phường Vĩnh Ninh", "Phường Vỹ Dạ", "Phường Xuân Phú"],
    },
    "Khánh Hòa": {
        "TP. Nha Trang": ["Phường Lộc Thọ", "Phường Ngọc Hiệp", "Phường Phước Hải", "Phường Phước Hòa", "Phường Phước Tân", "Phường Phước Tiến", "Phường Phương Sài", "Phường Phương Sơn", "Phường Tân Lập", "Phường Vạn Thắng", "Phường Vạn Thạnh", "Phường Vĩnh Hải", "Phường Vĩnh Hòa", "Phường Vĩnh Phước", "Phường Vĩnh Thọ", "Phường Vĩnh Nguyên", "Phường Vĩnh Trường", "Phường Xương Huân"],
    },
    "Lâm Đồng": {
        "TP. Đà Lạt": ["Phường 1", "Phường 2", "Phường 3", "Phường 4", "Phường 5", "Phường 6", "Phường 7", "Phường 8", "Phường 9", "Phường 10", "Phường 11", "Phường 12"],
        "TP. Bảo Lộc": ["Phường 1", "Phường 2", "Phường B'Lao", "Phường Lộc Phát", "Phường Lộc Sơn", "Phường Lộc Tiến"],
    },
}

# Bổ sung toàn bộ các tỉnh thành còn lại của Việt Nam để đủ 63 tỉnh thành
ALL_PROVINCES = [
    "Hà Nội", "TP. Hồ Chí Minh", "Đà Nẵng", "Hải Phòng", "Cần Thơ",
    "An Giang", "Bà Rịa - Vũng Tàu", "Bắc Giang", "Bắc Kạn", "Bạc Liêu", "Bắc Ninh", "Bến Tre", "Bình Định",
    "Bình Dương", "Bình Phước", "Bình Thuận", "Cà Mau", "Cao Bằng", "Đắk Lắk", "Đắk Nông", "Điện Biên",
    "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Giang", "Hà Nam", "Hà Tĩnh", "Hải Dương", "Hậu Giang",
    "Hòa Bình", "Hưng Yên", "Khánh Hòa", "Kiên Giang", "Kon Tum", "Lai Châu", "Lâm Đồng", "Lạng Sơn",
    "Lào Cai", "Long An", "Nam Định", "Nghệ An", "Ninh Bình", "Ninh Thuận", "Phú Thọ", "Phú Yên",
    "Quảng Bình", "Quảng Nam", "Quảng Ngãi", "Quảng Ninh", "Quảng Trị", "Sóc Trăng", "Sơn La", "Tây Ninh",
    "Thái Bình", "Thái Nguyên", "Thanh Hóa", "Thừa Thiên Huế", "Tiền Giang", "Trà Vinh", "Tuyên Quang",
    "Vĩnh Long", "Vĩnh Phúc", "Yên Bái"
]

class GeoService:
    def __init__(self):
        self.locations: List[Dict[str, Any]] = []
        self.province_by_code: Dict[int, Dict[str, Any]] = {}
        self.ward_by_prov_and_code: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._load_locations()

    def _load_locations(self):
        """Nạp tĩnh và cache dữ liệu địa giới 2 cấp từ vn_locations.json vào bộ nhớ."""
        import os, json
        data_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "vn_locations.json")
        if os.path.exists(data_file):
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    self.locations = json.load(f)
                for prov in self.locations:
                    p_code = prov.get("code")
                    if p_code is not None:
                        self.province_by_code[int(p_code)] = prov
                        for w in prov.get("wards", []):
                            w_code = w.get("code")
                            if w_code is not None:
                                self.ward_by_prov_and_code[(int(p_code), int(w_code))] = w
            except Exception as e:
                print(f"[GeoService] Lỗi nạp vn_locations.json: {e}")

    def get_all_locations(self) -> List[Dict[str, Any]]:
        """Trả về toàn bộ danh mục 34 tỉnh/thành phố và xã/phường/đặc khu (cache sẵn)."""
        return self.locations

    def validate_location(self, province_code: Any, ward_code: Any) -> Tuple[bool, Optional[str], Optional[dict], Optional[dict]]:
        """
        Xác thực ở server: ward_code phải thuộc đúng province_code đã chọn.
        Trả về: (is_valid, error_message, province_obj, ward_obj)
        """
        try:
            p_code = int(province_code)
            w_code = int(ward_code)
        except (ValueError, TypeError):
            return False, "Mã tỉnh/thành hoặc xã/phường không đúng định dạng", None, None

        prov = self.province_by_code.get(p_code)
        if not prov:
            return False, "Tỉnh/Thành phố không tồn tại trong danh mục", None, None

        ward = self.ward_by_prov_and_code.get((p_code, w_code))
        if not ward:
            return False, "Mã xã/phường không thuộc tỉnh/thành phố đã chọn", None, None

        return True, None, prov, ward

    @staticmethod
    def get_provinces() -> List[str]:
        return ALL_PROVINCES

    @staticmethod
    def get_districts(province: str) -> List[str]:
        if province in VIETNAM_GEO_DATA:
            return list(VIETNAM_GEO_DATA[province].keys())
        return ["Quận/Huyện trung tâm", "Thành phố / Thị xã", "Huyện ngoại thành"]

    @staticmethod
    def get_wards(province: str, district: str) -> List[str]:
        if province in VIETNAM_GEO_DATA and district in VIETNAM_GEO_DATA[province]:
            return VIETNAM_GEO_DATA[province][district]
        return ["Phường 1", "Phường 2", "Phường trung tâm", "Thị trấn", "Xã trung tâm"]


geo_service = GeoService()
