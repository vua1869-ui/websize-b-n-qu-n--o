import os

app_dir = r'app'
replaced_count = 0

for root, dirs, files in os.walk(app_dir):
    for file in files:
        if file.endswith('.html') or file.endswith('.js'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'font-serif' in content:
                new_content = content.replace('font-serif', 'font-sans')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                replaced_count += 1
                print(f"Replaced font-serif in {file}")

print(f"Total files updated: {replaced_count}")
