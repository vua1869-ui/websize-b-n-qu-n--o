#!/usr/bin/env python3
"""
scripts/export_locations.py
Xuất danh mục địa giới hành chính 2 cấp (Tỉnh/Thành phố -> Xã/Phường/Đặc khu)
từ thư viện vietnam-provinces (theo cấu trúc sau sáp nhập, 34 tỉnh/thành).
Chỉ trích xuất các trường cần thiết: code, name cho tỉnh và xã/phường.
"""
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import vietnam_provinces
except ImportError:
    print("Vui lòng cài đặt thư viện: pip install vietnam-provinces", file=sys.stderr)
    sys.exit(1)


def export_vn_locations(output_path: str):
    nested_path = vietnam_provinces.NESTED_DIVISIONS_JSON_PATH
    with open(nested_path, "r", encoding="utf-8") as f:
        provinces_raw = json.load(f)

    clean_locations = []
    total_wards = 0

    for prov in provinces_raw:
        prov_code = prov.get("code")
        prov_name = prov.get("name")
        raw_wards = prov.get("wards", [])

        clean_wards = []
        for w in raw_wards:
            clean_wards.append({
                "code": w.get("code"),
                "name": w.get("name")
            })

        total_wards += len(clean_wards)
        clean_locations.append({
            "code": prov_code,
            "name": prov_name,
            "wards": clean_wards
        })

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(clean_locations, f, ensure_ascii=False, indent=2)

    print(f"Đã xuất thành công {len(clean_locations)} tỉnh/thành phố và {total_wards} xã/phường/đặc khu.")
    print(f"File lưu tại: {output_path}")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target = os.path.join(base_dir, "app", "data", "vn_locations.json")
    export_vn_locations(target)
