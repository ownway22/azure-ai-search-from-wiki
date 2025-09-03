r"""
彙整 wiki-export 目錄下的所有 .md 檔案，輸出為 it_knowledge.json。

每個元素包含：
    - id：遞增的字串型 ID
    - file_name：Markdown 檔名（例：credentials.md）
    - category：{Networking, Security, DevOps} 其中之一
    - type：{code, meeting_notes, knowledge, credentials, others} 其中之一
    - content：檔案內容（UTF-8）

假設：
    - 若可由頂層資料夾名稱判別，則以該名稱推出 Category。
        若無法判斷（如位於根目錄），則使用簡單關鍵字啟發式，最後預設為 DevOps。
    - Type 依檔名樣式與類副檔名後綴進行推斷。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal


RepoRoot = Path(__file__).resolve().parents[1]
WIKI_DIR = RepoRoot / "wiki-export"
# 預設輸出在 scripts 旁，以避免缺少 'templates/' 目錄時造成問題
OUTPUT_JSON = RepoRoot / "scripts" / "it_knowledge.json"


Category = Literal["Networking", "Security", "DevOps"]
Type = Literal["code", "meeting_notes", "knowledge", "credentials", "others"]


def infer_category(path: Path, content: str) -> Category:
    parts = path.parts
    # 預期路徑型式為 wiki-export/<Category>/...
    try:
        idx = parts.index("wiki-export")
        if len(parts) > idx + 1:
            top = parts[idx + 1]
            if top in {"Networking", "Security", "DevOps"}:
                return top  # type: ignore[return-value]
    except ValueError:
        pass

    # 後援：根據內容以關鍵字進行啟發式判斷
    lower = content.lower()
    if any(k in lower for k in ("vpn", "subnet", "network", "gateway", "cidr", "dns")):
        return "Networking"  # type: ignore[return-value]
    if any(k in lower for k in ("incident", "vulnerability", "threat", "siem", "soc", "security")):
        return "Security"  # type: ignore[return-value]
    # Default
    return "DevOps"  # type: ignore[return-value]


def infer_type(file_name: str, content: str) -> Type:
    base = file_name.lower()
    if "meeting-notes" in base or "meeting_notes" in base:
        return "meeting_notes"
    if base.startswith("knowledge") or "knowledge-" in base:
        return "knowledge"
    if "credentials" in base:
        return "credentials"
    # 類程式碼檔（例如 sample.py.md、script.ps1.md）
    if re.search(r"\.(py|ps1|sh|js|ts|yaml|yml|json|xml|cs)\.md$", base):
        return "code"
    if base == "index.md":
        return "others"
    return "others"


def collect_md_files(root: Path) -> list[Path]:
    return sorted([p for p in root.rglob("*.md") if p.is_file()])


def main() -> int:
    if not WIKI_DIR.exists():
        print(f"ERROR: Input folder not found: {WIKI_DIR}")
        return 2

    files = collect_md_files(WIKI_DIR)
    items = []
    i = 0
    for fp in files:
    # 排除 Home.md 與 index.md
        if fp.name.lower() in {"home.md", "index.md"}:
            continue
        i += 1
        try:
            content = fp.read_text(encoding="utf-8")
        except Exception:
            content = ""

        cat = infer_category(fp, content)
        t = infer_type(fp.name, content)
        items.append(
            {
                "id": str(i),
                "file_name": fp.name,
                "category": cat,
                "type": t,
                "content": content,
            }
        )

    # Ensure output directory exists
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Wrote {len(items)} items to {OUTPUT_JSON}")
    # Show a brief breakdown
    by_cat = {"Networking": 0, "Security": 0, "DevOps": 0}
    for it in items:
        by_cat[it["category"]] += 1
    print("[i] Counts by category:", by_cat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
