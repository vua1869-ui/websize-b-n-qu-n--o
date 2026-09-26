import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from app.services.product_service import CATEGORY_META, product_service

DEFAULT_COLOR_HEX: Dict[str, str] = {
    "đen": "#18181b", "den": "#18181b", "black": "#18181b",
    "trắng": "#ffffff", "trang": "#ffffff", "white": "#ffffff",
    "be": "#f5f5dc", "kem": "#fffdd0", "nâu": "#8b5a2b", "nau": "#8b5a2b",
    "xanh": "#2563eb", "xanh dương": "#1d4ed8", "xanh navy": "#1e3a8a", "navy": "#1e3a8a",
    "xanh lá": "#16a34a", "xanh mint": "#a7f3d0", "mint": "#a7f3d0",
    "đỏ": "#dc2626", "do": "#dc2626", "đỏ rượu": "#881337", "hồng": "#f472b6", "hong": "#f472b6",
    "vàng": "#eab308", "vang": "#eab308", "xám": "#71717a", "xam": "#71717a",
    "ghi": "#9ca3af", "cam": "#ea580c", "tím": "#9333ea", "tim": "#9333ea",
}

CATEGORY_ALIASES: Dict[str, str] = {
    "ao_thun": "ao_thun", "ao-thun": "ao_thun", "aothun": "ao_thun",
    "ao_so_mi": "ao_so_mi", "ao-so-mi": "ao_so_mi", "so_mi": "ao_so_mi", "so-mi": "ao_so_mi",
    "ao_khoac": "ao_khoac", "ao-khoac": "ao_khoac", "blazer": "ao_khoac",
    "quan_tay": "quan_tay", "quan-tay": "quan_tay", "quan_dai": "quan_tay", "quan-dai": "quan_tay",
    "quan_jeans": "quan_jeans", "quan-jeans": "quan_jeans", "jeans": "quan_jeans",
    "chan_vay": "chan_vay", "chan-vay": "chan_vay",
    "vay_dam": "vay_dam", "vay-dam": "vay_dam", "dam": "vay_dam", "vay": "vay_dam",
    "set_do": "set_do", "set-do": "set_do",
    "phu_kien": "phu_kien", "phu-kien": "phu_kien",
}


def parse_colors(raw: Optional[str]) -> List[Dict[str, str]]:
    if not raw or not str(raw).strip():
        return [{"name": "Tiêu chuẩn", "hex": "#27272a"}]
    result = []
    items = [c.strip() for c in str(raw).split(",") if c.strip()]
    for item in items:
        if ":" in item:
            parts = item.split(":", 1)
            c_name = parts[0].strip()
            c_hex = parts[1].strip()
            if not c_hex.startswith("#"):
                c_hex = f"#{c_hex}"
            result.append({"name": c_name, "hex": c_hex})
        else:
            c_name = item.strip()
            c_hex = DEFAULT_COLOR_HEX.get(c_name.lower(), "#27272a")
            result.append({"name": c_name, "hex": c_hex})
    return result if result else [{"name": "Tiêu chuẩn", "hex": "#27272a"}]


def parse_split_list(raw: Optional[str], sep: str = ",") -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [item.strip() for item in str(raw).split(sep) if item.strip()]


class ProductImportService:
    VALID_CATEGORIES = list(CATEGORY_META.keys())

    def parse_file(self, content: bytes, filename: str) -> List[Dict[str, Any]]:
        """
        Đọc nội dung file CSV hoặc Excel (.xlsx) thành danh sách dictionary từng dòng.
        """
        lower_name = filename.lower()
        if lower_name.endswith(".csv"):
            return self._parse_csv(content)
        elif lower_name.endswith(".xlsx") or lower_name.endswith(".xls"):
            return self._parse_excel(content)
        else:
            raise ValueError("Định dạng file không hỗ trợ. Vui lòng tải lên file .csv hoặc .xlsx")

    def _parse_csv(self, content: bytes) -> List[Dict[str, Any]]:
        # Thử utf-8-sig (hỗ trợ BOM) rồi utf-8
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("cp1258", errors="replace")

        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for r in reader:
            # Chuẩn hóa tên cột: lowercase và strip
            cleaned_row = {
                (k.strip().lower() if k else ""): (v.strip() if v else "")
                for k, v in r.items()
                if k
            }
            rows.append(cleaned_row)
        return rows

    def _parse_excel(self, content: bytes) -> List[Dict[str, Any]]:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        sheet = wb.active
        if sheet is None:
            return []

        rows_iter = sheet.iter_rows(values_only=True)
        try:
            headers = next(rows_iter)
        except StopIteration:
            return []

        if not headers:
            return []

        cleaned_headers = [str(h).strip().lower() if h is not None else "" for h in headers]

        rows = []
        for raw_row in rows_iter:
            if not any(raw_row):
                continue
            row_dict = {}
            for col_idx, h in enumerate(cleaned_headers):
                if not h:
                    continue
                val = raw_row[col_idx] if col_idx < len(raw_row) else None
                row_dict[h] = str(val).strip() if val is not None else ""
            rows.append(row_dict)
        return rows

    def validate_and_normalize_row(self, row: Dict[str, Any], row_idx: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Kiểm tra và chuẩn hóa dữ liệu một dòng.
        Trả về (normalized_dict, None) nếu hợp lệ, hoặc (None, error_message) nếu lỗi.
        """
        # 1. Tên sản phẩm
        name = row.get("name") or row.get("tên") or row.get("ten_san_pham") or ""
        name = str(name).strip()
        if not name:
            return None, "Tên sản phẩm không được để trống"

        # 2. Danh mục
        raw_cat = row.get("category") or row.get("danh_mục") or row.get("danh_muc") or ""
        raw_cat = str(raw_cat).strip().lower().replace(" ", "_")
        canonical_cat = CATEGORY_ALIASES.get(raw_cat)
        if not canonical_cat:
            valid_cats_str = ", ".join(self.VALID_CATEGORIES)
            return None, f"Danh mục '{raw_cat}' không hợp lệ. Phải thuộc: {valid_cats_str}"

        # 3. Giới tính
        gender = (row.get("gender") or row.get("giới_tính") or row.get("gioi_tinh") or "unisex").strip().lower()
        if gender not in ["nam", "nu", "unisex"]:
            gender = "unisex"

        # 4. Giá bán
        raw_price = row.get("price") or row.get("giá") or row.get("gia") or "0"
        try:
            raw_price_str = str(raw_price).strip()
            is_neg = raw_price_str.startswith("-")
            clean_digits = re.sub(r"[^\d]", "", raw_price_str)
            if not clean_digits:
                return None, f"Giá bán '{raw_price}' không hợp lệ"
            price = -int(clean_digits) if is_neg else int(clean_digits)
            if price <= 0:
                return None, f"Giá bán '{raw_price}' phải là số nguyên dương lớn hơn 0"
        except Exception:
            return None, f"Giá bán '{raw_price}' không hợp lệ"

        # 5. Giá gốc (original_price)
        raw_orig_price = row.get("original_price") or row.get("giá_gốc") or row.get("gia_goc") or ""
        orig_price = price
        if raw_orig_price:
            try:
                raw_orig_str = str(raw_orig_price).strip()
                is_orig_neg = raw_orig_str.startswith("-")
                clean_orig = re.sub(r"[^\d]", "", raw_orig_str)
                if clean_orig:
                    parsed_orig = -int(clean_orig) if is_orig_neg else int(clean_orig)
                    orig_price = max(price, parsed_orig)
            except Exception:
                orig_price = price

        # 6. Tồn kho (stock)
        raw_stock = row.get("stock") or row.get("tồn_kho") or row.get("ton_kho") or "0"
        try:
            raw_stock_str = str(raw_stock).strip()
            is_stock_neg = raw_stock_str.startswith("-")
            clean_stock_digits = re.sub(r"[^\d]", "", raw_stock_str)
            if not clean_stock_digits:
                return None, f"Số lượng tồn kho '{raw_stock}' không hợp lệ"
            stock = -int(clean_stock_digits) if is_stock_neg else int(clean_stock_digits)
            if stock < 0:
                return None, f"Số lượng tồn kho '{raw_stock}' không được là số âm"
        except Exception:
            return None, f"Số lượng tồn kho '{raw_stock}' không hợp lệ"

        # 7. Sizes
        raw_sizes = row.get("sizes") or row.get("kích_thước") or row.get("size") or "S, M, L, XL"
        sizes = parse_split_list(raw_sizes, sep=",")
        if not sizes:
            sizes = ["S", "M", "L", "XL"]

        # 8. Colors
        raw_colors = row.get("colors") or row.get("màu") or row.get("mau") or ""
        colors = parse_colors(raw_colors)

        # 9. Occasions & Tags
        raw_occasions = row.get("occasions") or row.get("dịp") or row.get("dip") or ""
        occasions = parse_split_list(raw_occasions, sep=",")

        raw_tags = row.get("tags") or row.get("thẻ") or ""
        tags = parse_split_list(raw_tags, sep=",")

        # 10. Images (phân cách bằng '|')
        raw_images = row.get("images") or row.get("hình_ảnh") or row.get("hinh_anh") or row.get("image") or ""
        images = parse_split_list(raw_images, sep="|")

        # 11. Các trường mô tả bổ sung
        description = row.get("description") or row.get("mô_tả") or row.get("mo_ta") or name
        material = row.get("material") or row.get("chất_liệu") or row.get("chat_lieu") or "Cotton & Linen cao cấp"
        style = row.get("style") or row.get("phong_cách") or row.get("phong_cach") or "Hiện đại"

        # ID (nếu có để thực hiện upsert)
        raw_id = (row.get("id") or row.get("mã") or row.get("ma_san_pham") or "").strip()

        normalized = {
            "id": raw_id if raw_id else None,
            "name": name,
            "category": canonical_cat,
            "category_name": CATEGORY_META[canonical_cat][0],
            "gender": gender,
            "price": price,
            "original_price": orig_price,
            "stock": stock,
            "stock_total": max(stock, 100),
            "sizes": sizes,
            "colors": colors,
            "occasions": occasions,
            "tags": tags,
            "images": images,
            "description": description,
            "material": material,
            "style": style,
        }
        return normalized, None

    def import_products(self, file_content: bytes, filename: str) -> Dict[str, Any]:
        """
        Nhập sản phẩm từ file CSV hoặc Excel.
        Validate từng dòng: dòng hợp lệ được import/upsert, dòng lỗi được ghi nhận và báo cáo.
        """
        rows = self.parse_file(file_content, filename)
        if not rows:
            return {
                "total_rows": 0,
                "success_count": 0,
                "failed_count": 0,
                "imported_products": [],
                "errors": [{"row": 1, "error": "File rỗng hoặc không chứa dữ liệu hợp lệ"}],
            }

        imported_products = []
        errors = []

        for idx, row in enumerate(rows, start=2):  # Dòng 1 là tiêu đề, dữ liệu bắt đầu từ dòng 2
            norm_data, err = self.validate_and_normalize_row(row, idx)
            if err:
                errors.append({
                    "row": idx,
                    "product_name": row.get("name") or "(Không tên)",
                    "error": err,
                })
                continue

            target_id = norm_data.get("id")
            existing_prod = product_service.get_by_id(target_id) if target_id else None

            try:
                if existing_prod:
                    # Upsert: Cập nhật sản phẩm đã tồn tại
                    updated = product_service.update_product(target_id, norm_data)
                    imported_products.append(updated.model_dump())
                else:
                    # Tạo sản phẩm mới
                    created = product_service.create_product(norm_data)
                    imported_products.append(created.model_dump())
            except Exception as e:
                errors.append({
                    "row": idx,
                    "product_name": norm_data.get("name"),
                    "error": f"Lỗi lưu trữ: {str(e)}",
                })

        return {
            "total_rows": len(rows),
            "success_count": len(imported_products),
            "failed_count": len(errors),
            "imported_products": imported_products,
            "errors": errors,
        }


product_import_service = ProductImportService()
