import os

css_extra = """
/* ── Additional CSS Helpers & Responsive Rules ── */
.list-none { list-style-type: none; }
.no-print { }
@media print {
  .no-print { display: none !important; }
  .invoice-print-area { display: block !important; }
}
.ring-offset-2 { ring-offset-width: 2px; }
.placeholder\:text-\[\#999999\]::placeholder,
.placeholder\:text-noir-400::placeholder { color: #888888 !important; }
.hover\:ring-\[\#111111\]:hover { box-shadow: 0 0 0 1px #111111 !important; }
.focus\:ring-gold-500\/50:focus { box-shadow: 0 0 0 2px rgba(17,17,17,0.5) !important; }

@media (min-width: 640px) {
  .sm\:\!text-sm { font-size: 0.875rem !important; line-height: 1.25rem !important; }
  .sm\:aspect-\[21\/9\] { aspect-ratio: 21 / 9 !important; }
  .sm\:flex-initial { flex: 0 1 auto; }
  .sm\:h-10 { height: 2.5rem; }
  .sm\:h-12 { height: 3rem; }
  .sm\:h-13 { height: 3.25rem; }
  .sm\:h-20 { height: 5rem; }
  .sm\:h-52 { height: 13rem; }
  .sm\:items-center { align-items: center; }
  .sm\:justify-center { justify-content: center; }
  .sm\:max-h-\[460px\] { max-height: 460px; }
  .sm\:mx-auto { margin-left: auto; margin-right: auto; }
  .sm\:w-10 { width: 2.5rem; }
  .sm\:w-12 { width: 3rem; }
}

@media (min-width: 768px) {
  .md\:w-72 { width: 18rem; }
}

@media (min-width: 1024px) {
  .lg\:border-r { border-right-width: 1px; }
}
"""

with open(r'app/static/css/custom.css', 'a', encoding='utf-8') as fp:
    fp.write("\n" + css_extra)

print("Appended extra responsive & helper rules to custom.css!")
