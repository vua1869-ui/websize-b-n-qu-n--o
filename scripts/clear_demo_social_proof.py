#!/usr/bin/env python3
"""Script đặt rating=0, reviews_count=0, sold_count=0 cho các sản phẩm không có dữ liệu thật trong DB."""
import argparse
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "app", "data", "aura_store.db")
JSON_PATH = os.path.join(BASE_DIR, "app", "data", "products.json")


def main():
    parser = argparse.ArgumentParser(description="Xóa dữ liệu xã hội ảo (rating, reviews_count, sold_count) cho sản phẩm không có đánh giá thật.")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử không ghi dữ liệu vào CSDL hay file JSON.")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"Không tìm thấy CSDL tại {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Lấy các product_id có đánh giá thật
    cursor.execute("SELECT DISTINCT product_id FROM reviews;")
    products_with_reviews = {row["product_id"] for row in cursor.fetchall()}

    # Lấy tất cả sản phẩm trong DB
    cursor.execute("SELECT id, rating, reviews_count, sold_count FROM products;")
    all_db_products = cursor.fetchall()

    to_update_ids = []
    for p in all_db_products:
        pid = p["id"]
        if pid not in products_with_reviews:
            if p["rating"] > 0 or p["reviews_count"] > 0 or p["sold_count"] > 0:
                to_update_ids.append(pid)

    print(f"Tổng số sản phẩm trong DB: {len(all_db_products)}")
    print(f"Số sản phẩm có đánh giá thật: {len(products_with_reviews)}")
    print(f"Số sản phẩm ảo cần đặt lại về 0: {len(to_update_ids)}")

    if args.dry_run:
        print("[DRY-RUN] Không thực hiện thay đổi dữ liệu.")
        conn.close()
        return

    # Cập nhật trong SQLite
    if to_update_ids:
        placeholders = ",".join("?" for _ in to_update_ids)
        cursor.execute(
            f"UPDATE products SET rating = 0.0, reviews_count = 0, sold_count = 0 WHERE id IN ({placeholders});",
            to_update_ids
        )
        conn.commit()
        print(f"Đã cập nhật {cursor.rowcount} sản phẩm trong SQLite.")

    conn.close()

    # Cập nhật trong products.json
    if os.path.exists(JSON_PATH) and to_update_ids:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            prods = json.load(f)

        updated_json_count = 0
        update_set = set(to_update_ids)
        for p in prods:
            if p.get("id") in update_set:
                p["rating"] = 0.0
                p["reviews_count"] = 0
                p["sold_count"] = 0
                updated_json_count += 1

        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(prods, f, ensure_ascii=False, indent=2)
        print(f"Đã cập nhật {updated_json_count} sản phẩm trong {JSON_PATH}.")

    print("Hoàn tất dọn dẹp social proof ảo!")


if __name__ == "__main__":
    main()
