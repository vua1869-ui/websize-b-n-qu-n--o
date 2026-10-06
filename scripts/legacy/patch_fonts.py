import os, re

templates_dir = r'c:\Users\lenk\Downloads\fashion_ai_store\app\templates'
files = ['login.html', 'register.html', 'product-detail.html', 'order-success.html', 'profile.html', '403.html', '404.html']

font_link_pattern = re.compile(
    r'[ \t]*<link rel="preconnect" href="https://fonts\.googleapis\.com"[^>]*/>\s*'
    r'<link rel="preconnect" href="https://fonts\.gstatic\.com"[^>]*/>\s*'
    r'<link href="https://fonts\.googleapis\.com/css2\?[^"]*"[^>]*/>',
    re.DOTALL
)

replacement = '  {%- include "components/fonts.html" -%}'

for f in files:
    path = os.path.join(templates_dir, f)
    if not os.path.exists(path):
        print(f'Not found: {f}')
        continue
    with open(path, 'r', encoding='utf-8') as fh:
        content = fh.read()
    if 'fonts.googleapis.com' in content:
        new_content = font_link_pattern.sub(replacement, content)
        if new_content != content:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(new_content)
            print(f'Patched: {f}')
        else:
            print(f'Pattern not matched: {f}')
    else:
        print(f'Skip (no font link): {f}')

print('Done')
