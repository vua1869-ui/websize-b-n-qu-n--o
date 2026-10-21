import os

extra_css = """
/* ── Layout & Responsive Rules for 1600px Container & Product Gallery ── */
.max-w-\\[1600px\\] { max-width: 1600px !important; }
.flex-col-reverse { flex-direction: column-reverse !important; }
.max-h-\\[600px\\] { max-height: 600px !important; }
.mb-12 { margin-bottom: 3rem !important; }
.pt-10 { padding-top: 2.5rem !important; }
.pt-12 { padding-top: 3rem !important; }

@media (min-width: 640px) {
  .sm\\:pt-14 { padding-top: 3.5rem !important; }
}

@media (min-width: 1024px) {
  .lg\\:px-10 { padding-left: 2.5rem !important; padding-right: 2.5rem !important; }
  .lg\\:flex-col { flex-direction: column !important; }
  .lg\\:gap-12 { gap: 3rem !important; }
  .lg\\:h-20 { height: 5rem !important; }
  .lg\\:w-16 { width: 4rem !important; }
  .lg\\:w-auto { width: auto !important; }
  .lg\\:overflow-y-auto { overflow-y: auto !important; }
}

@media (min-width: 1536px) {
  .\\32 xl\\:px-16 { padding-left: 4rem !important; padding-right: 4rem !important; }
}
"""

with open(r'app/static/css/custom.css', 'a', encoding='utf-8') as f:
    f.write("\n" + extra_css)

print("Appended 1600px & layout CSS rules to custom.css")
