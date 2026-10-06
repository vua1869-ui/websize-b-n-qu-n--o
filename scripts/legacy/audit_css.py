import os
import re

template_dir = r'app/templates'
css_files = [r'app/static/css/tailwind.css', r'app/static/css/custom.css']

css_content = ''
for f in css_files:
    if os.path.exists(f):
        with open(f, 'r', encoding='utf-8') as fp:
            css_content += fp.read() + '\n'

classes_in_templates = set()
for root, dirs, files in os.walk(template_dir):
    for file in files:
        if file.endswith('.html'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as fp:
                content = fp.read()
                # find class="..." or class='...'
                matches = re.findall(r'class=["\']([^"\']+)["\']', content)
                for m in matches:
                    m_clean = re.sub(r'\{\{[^\}]+\}\}', '', m)
                    m_clean = re.sub(r'\{%[^\%]+\%\}', '', m_clean)
                    for cls in m_clean.split():
                        cls = cls.strip()
                        if cls and not cls.startswith('{') and not cls.startswith('%') and not '$' in cls and not '?' in cls and not '=' in cls and not '<' in cls and not '>' in cls:
                            classes_in_templates.add(cls)

print(f'Total unique clean classes in templates: {len(classes_in_templates)}')

missing = []
for cls in sorted(classes_in_templates):
    css_escaped = cls.replace(':', r'\:').replace('[', r'\[').replace(']', r'\]').replace('/', r'\/').replace('.', r'\.').replace('%', r'\%')
    if cls not in css_content and css_escaped not in css_content:
        missing.append(cls)

print(f'Missing classes count: {len(missing)}')
with open('missing_classes.txt', 'w', encoding='utf-8') as out:
    for cls in missing:
        out.write(cls + '\n')
print("Wrote missing classes to missing_classes.txt")
