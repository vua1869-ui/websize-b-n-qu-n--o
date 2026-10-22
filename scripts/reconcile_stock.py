"""
Script đối soát tồn kho giữa CSDL products, product_variants và bộ nhớ RAM.
Mặc định chỉ đọc (dry-run). Sử dụng cờ --apply để áp dụng sửa đổi.
"""
import argparse
import os
import sys

# Đảm bảo import được app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.stock_reconcile import reconcile_stock  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="AURA Studio - Đối soát tồn kho RAM và CSDL")
    parser.add_argument("--apply", action="store_true", help="Áp dụng sửa lỗi đồng bộ tồn kho")
    args = parser.parse_args()

    mode_str = "APPLY (SỬA DỮ LIỆU)" if args.apply else "DRY-RUN (CHỈ ĐỌC)"
    print(f"\n=== ĐỐI SOÁT TỒN KHO [{mode_str}] ===")

    report = reconcile_stock(apply_fix=args.apply, admin_id="cli_reconcile")

    pv_mismatches = report["product_variant_mismatches"]
    negatives = report["negative_variants"]
    ram_mismatches = report["ram_mismatches"]

    has_issue = report["has_mismatch"]

    if pv_mismatches:
        print("\n[!] PHÁT HIỆN LỆCH GIỮA products.stock VÀ TỔNG product_variants.stock:")
        print(f"{'Product ID':<12} | {'Tên sản phẩm':<30} | {'DB Stock':<10} | {'Sum Variants':<12} | {'Chênh lệch':<10}")
        print("-" * 85)
        for m in pv_mismatches:
            print(f"{m['product_id']:<12} | {m['name'][:30]:<30} | {m['product_stock']:<10} | {m['variants_sum']:<12} | {m['diff']:<10}")

    if negatives:
        print("\n[!] PHÁT HIỆN BIẾN THỂ CÓ TỒN KHO ÂM (stock < 0):")
        print(f"{'Variant ID':<10} | {'Product ID':<12} | {'Size':<6} | {'Color':<12} | {'Tồn kho':<8}")
        print("-" * 55)
        for n in negatives:
            print(f"{n['variant_id']:<10} | {n['product_id']:<12} | {str(n.get('size')):<6} | {str(n.get('color')):<12} | {n['stock']:<8}")

    if ram_mismatches:
        print("\n[!] PHÁT HIỆN LỆCH GIỮA BỘ NHỚ RAM VÀ CSDL:")
        print(f"{'Product ID':<12} | {'Tên sản phẩm':<30} | {'RAM Stock':<10} | {'DB Stock':<10} | {'Chênh lệch':<10}")
        print("-" * 80)
        for r in ram_mismatches:
            print(f"{r['product_id']:<12} | {r['name'][:30]:<30} | {r['ram_stock']:<10} | {r['db_stock']:<10} | {r['diff']:<10}")

    print("\n" + "=" * 50)
    print(f"TỔNG KẾT: {report['summary']}")
    if args.apply:
        print("[✓] Đã áp dụng cập nhật CSDL và ghi nhận audit log thành công.")
        sys.exit(0)
    else:
        if has_issue:
            print("[!] Có chênh lệch tồn kho. Chạy lại với cờ '--apply' để tự động sửa.")
            sys.exit(1)
        else:
            print("[✓] Toàn bộ tồn kho hoàn toàn đồng nhất!")
            sys.exit(0)


if __name__ == "__main__":
    main()
