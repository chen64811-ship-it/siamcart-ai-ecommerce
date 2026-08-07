#!/usr/bin/env python
"""Extract the PRODUCTS array from store.js into JSON for seeding (Task 5C)."""
import json
import re
import sys
from pathlib import Path

src = Path("app/static/js/store.js").read_text(encoding="utf-8")

start = src.index("var PRODUCTS = [")
end = src.index("];", start) + 2
array_js = src[start:end].split("=", 1)[1].strip()
array_js = array_js.rstrip().rstrip(";").strip()

FIELDS = [
    "id", "name", "shortDescription", "fullDescription", "category",
    "price", "originalPrice", "rating", "reviewCount", "stock",
    "badge", "newInDays", "image", "thumbnails", "features",
]

# key: -> "key": (consume the colon so it is not duplicated)
pattern = re.compile(r"\b(" + "|".join(FIELDS) + r")\s*:", re.MULTILINE)
array_js = pattern.sub(lambda m: '"' + m.group(1) + '":', array_js)

try:
    products = json.loads(array_js)
except Exception as e:
    print("JSON parse failed:", e, file=sys.stderr)
    sys.exit(1)

print("COUNT:", len(products))
for p in products:
    print(json.dumps(p, ensure_ascii=False))
