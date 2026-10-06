import urllib.request
import json
import sys

# Configure stdout for utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def verify():
    with urllib.request.urlopen("http://127.0.0.1:8000/") as r:
        html = r.read().decode("utf-8")
        assert "app.js?v=2.1.0" in html, "app.js version missing!"
        assert "ai_stylist.js?v=2.1.0" in html, "ai_stylist.js version missing!"
        print("[OK] Homepage HTML contains updated script cache busters (v=2.1.0).")

    with urllib.request.urlopen("http://127.0.0.1:8000/api/trends?limit=5") as r:
        trends = json.loads(r.read().decode("utf-8"))
        print(f"[OK] /api/trends returned {len(trends)} trends. Top keyword: '{trends[0]['keyword']}' (source: {trends[0]['source']})")

    with urllib.request.urlopen("http://127.0.0.1:8000/api/trending-products?limit=4") as r:
        prods = json.loads(r.read().decode("utf-8"))
        print(f"[OK] /api/trending-products returned {len(prods)} products.")
        for p in prods:
            print(f"   - #{p['rank']} {p['product']['name']} (Stock: {p['product']['stock']}, Score: {p['final_score']}, Ly do: {p['reason']})")

    print("\nALL SERVER CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    verify()
