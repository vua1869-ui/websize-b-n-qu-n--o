import os
import re

css_rules_base = []
css_rules_sm = []
css_rules_md = []
css_rules_lg = []
css_rules_xl = []

def escape_cls(cls):
    return cls.replace(':', r'\:').replace('[', r'\[').replace(']', r'\]').replace('/', r'\/').replace('.', r'\.').replace('%', r'\%').replace('!', r'\!')

with open('missing_classes.txt', 'r', encoding='utf-8') as f:
    classes = [line.strip() for line in f if line.strip()]

for cls in classes:
    if not cls or cls in ('||', 'isCurr', 'selected_category', 'selected_gender', 'selected_sort', 'active_page', 'current_page'):
        continue

    prefix = ""
    raw_cls = cls
    if cls.startswith("sm:"):
        prefix = "sm"
        raw_cls = cls[3:]
    elif cls.startswith("md:"):
        prefix = "md"
        raw_cls = cls[3:]
    elif cls.startswith("lg:"):
        prefix = "lg"
        raw_cls = cls[3:]
    elif cls.startswith("xl:"):
        prefix = "xl"
        raw_cls = cls[3:]

    escaped = escape_cls(cls)
    rule = ""

    if raw_cls in ("!text-sm", "text-sm", "sm:!text-sm", "!text-xs", "text-xs", "text-[7px]", "text-[8px]", "text-[9px]", "text-[10px]", "text-[11px]", "text-[12px]", "text-[13px]"):
        rule = f".{escaped} {{ font-size: 0.875rem !important; line-height: 1.5 !important; }}" # 14px minimum
    elif raw_cls == "text-[15px]" or raw_cls == "sm:text-[15px]":
        rule = f".{escaped} {{ font-size: 0.9375rem !important; line-height: 1.55 !important; }}" # 15px
    elif raw_cls == "text-body" or raw_cls == "text-base":
        rule = f".{escaped} {{ font-size: 1rem !important; line-height: 1.6 !important; }}" # 16px
    elif raw_cls == "text-body-sm":
        rule = f".{escaped} {{ font-size: 0.9375rem !important; line-height: 1.55 !important; }}" # 15px
    elif raw_cls == "text-label":
        rule = f".{escaped} {{ font-size: 0.875rem !important; line-height: 1.5 !important; }}" # 14px
    elif raw_cls == "text-h3" or raw_cls == "heading-card":
        rule = f".{escaped} {{ font-size: 1.125rem !important; line-height: 1.4 !important; font-weight: 600; }}" # 18px
    elif raw_cls == "text-h2" or raw_cls == "heading-section":
        rule = f".{escaped} {{ font-size: 1.5rem !important; line-height: 1.3 !important; font-weight: 600; }}" # 24px
    elif raw_cls == "text-h1" or raw_cls == "heading-page":
        rule = f".{escaped} {{ font-size: 1.75rem !important; line-height: 1.25 !important; font-weight: 600; }}" # 28px
    elif raw_cls == "heading-hero":
        rule = f".{escaped} {{ font-size: 2.25rem !important; line-height: 1.2 !important; font-weight: 600; }}" # 36px
    elif raw_cls == "price":
        rule = f".{escaped} {{ font-size: 1.75rem !important; line-height: 1.2 !important; font-weight: 600; }}" # 28px

    if rule:
        if prefix == "sm": css_rules_sm.append(rule)
        elif prefix == "md": css_rules_md.append(rule)
        elif prefix == "lg": css_rules_lg.append(rule)
        elif prefix == "xl": css_rules_xl.append(rule)
        else: css_rules_base.append(rule)

out_css = "\n/* ── Generated Urban Revivo Typography Rules (Min 14px) ── */\n"
out_css += "\n".join(css_rules_base) + "\n"

if css_rules_sm: out_css += "\n@media (min-width: 640px) {\n  " + "\n  ".join(css_rules_sm) + "\n}\n"
if css_rules_md: out_css += "\n@media (min-width: 768px) {\n  " + "\n  ".join(css_rules_md) + "\n}\n"
if css_rules_lg: out_css += "\n@media (min-width: 1024px) {\n  " + "\n  ".join(css_rules_lg) + "\n}\n"
if css_rules_xl: out_css += "\n@media (min-width: 1280px) {\n  " + "\n  ".join(css_rules_xl) + "\n}\n"

with open(r'app/static/css/custom.css', 'a', encoding='utf-8') as fp:
    fp.write("\n" + out_css)

print("Appended clean urban revivo typography rules to custom.css!")
