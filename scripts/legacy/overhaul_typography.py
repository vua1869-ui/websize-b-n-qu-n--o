import os
import re

app_dir = r'app'

# Helper replacements for regex pattern matches
replacements_in_files = [
    # 1. Font weights
    (r'\bfont-extrabold\b', 'font-bold'),
    (r'\bfont-black\b', 'font-bold'),
    
    # 2. Small font sizes (convert text-xs, text-[7..13px] to text-sm or text-label)
    (r'\btext-xs\b', 'text-sm'),
    (r'\btext-\[7px\]\b', 'text-sm'),
    (r'\btext-\[8px\]\b', 'text-sm'),
    (r'\btext-\[9px\]\b', 'text-sm'),
    (r'\btext-\[10px\]\b', 'text-sm'),
    (r'\btext-\[11px\]\b', 'text-sm'),
    (r'\btext-\[12px\]\b', 'text-sm'),
    (r'\btext-\[13px\]\b', 'text-sm'),
    (r'\btext-\[15px\]\b', 'text-base'),
    (r'\btext-\[34px\]\b', 'text-3xl'),

    # 3. Uppercase cleanup on non-menu/non-button elements
    # Remove uppercase from breadcrumb, th, chip, tag, field-label where appropriate
    (r'(\bfield-label\b[^"\']*)\buppercase\b', r'\1'),
    (r'(\bchip\b[^"\']*)\buppercase\b', r'\1'),
    (r'(\btag\b[^"\']*)\buppercase\b', r'\1'),
]

for root, dirs, files in os.walk(app_dir):
    for file in files:
        if file.endswith('.html') or file.endswith('.js'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content
            for pat, repl in replacements_in_files:
                new_content = re.sub(pat, repl, new_content)

            # Preserving badge numbers on cart / wishlist badges if needed, but text-sm (14px) or text-[11px] mapped to 14px is fine!
            if new_content != content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Updated typography in {file}")

print("Typography overhaul completed across templates and scripts!")
