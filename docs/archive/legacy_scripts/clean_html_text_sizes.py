import os
import re

app_dir = r'app'

def clean_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content
    # Replace text-[7px] to text-[13px] and text-xs with text-sm
    new_content = re.sub(r'text-\[7px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[8px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[9px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[10px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[11px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[12px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[13px\]', 'text-sm', new_content)
    new_content = re.sub(r'text-\[15px\]', 'text-base', new_content)

    if new_content != content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Cleaned {path}")

for root, dirs, files in os.walk(app_dir):
    for file in files:
        if file.endswith('.html') or file.endswith('.js'):
            clean_file(os.path.join(root, file))

print("Finished cleaning literal text-[px] strings.")
