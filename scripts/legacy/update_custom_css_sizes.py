import re

with open(r'app/static/css/custom.css', 'r', encoding='utf-8') as f:
    css = f.read()

# Replace 0.75rem font sizes with 0.875rem (14px), 0.8125rem with 0.9375rem (15px)
css = re.sub(r'font-size:\s*0\.75rem;', 'font-size: 0.875rem;', css)
css = re.sub(r'font-size:\s*0\.8125rem;', 'font-size: 0.9375rem;', css)

# Replace .btn font size with 0.9375rem (15px)
css = re.sub(r'(\.btn\s*\{[^}]*font-size:\s*)[^;]+;', r'\1 0.9375rem;', css)
css = re.sub(r'(\.btn-sm\s*\{[^}]*font-size:\s*)[^;]+;', r'\1 0.875rem;', css)

# Fix badge size if needed
css = re.sub(r'font-family:\s*\'Playfair Display\',[^;]+;', "font-family: var(--font-sans);", css)

with open(r'app/static/css/custom.css', 'w', encoding='utf-8') as f:
    f.write(css)

print("Updated custom.css font sizes and font families to minimum 14px (0.875rem) / 15px (0.9375rem)!")
