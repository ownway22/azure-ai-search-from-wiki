r"""
自 Azure DevOps Project Wiki 下載所有頁面，並輸出到本機資料夾，
且維持與 Wiki 頁面階層一致的目錄結構。

透過 .env 或系統環境變數進行設定：
    - AZDO_ORG_URL：例如 https://dev.azure.com/<org>
    - AZDO_PROJECT：例如 <project>
    - AZDO_WIKI：Wiki 名稱（Azure DevOps 建立的 Project Wiki 通常以 .wiki 結尾）
    - AZDO_PAT：具備 Wiki 讀取權限的 Personal Access Token
    - OUTPUT_DIR：本機匯出資料夾（預設：./wiki-export）

範例（PowerShell）：
        $env:AZDO_PAT = "<your_pat>"
        python .\scripts\01_download_from_wiki.py

注意事項：
    - 使用 REST API 版本 7.2-preview（部分端點需要）。
    - 每個 Wiki 頁面輸出成一個 .md 檔。子頁面會成為子資料夾。
    - 會清理 Windows 不允許的檔名字元。
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

import requests
from dotenv import load_dotenv

API_VERSION = "7.2-preview"


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v else default


def get_auth_header(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def sanitize_segment(name: str) -> str:
    # 取代 Windows 檔名不允許的字元
    illegal = '<>:"/\\|?*'
    sanitized = "".join((c if c not in illegal else "_") for c in name)
    sanitized = sanitized.strip().rstrip('.')  # 避免結尾空白與句點
    return sanitized or "untitled"


def ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def find_wiki(org_url: str, project: str, wiki_name: Optional[str], headers: dict) -> Optional[dict]:
    url = f"{org_url}/{project}/_apis/wiki/wikis"
    r = requests.get(url, headers=headers, params={"api-version": API_VERSION}, timeout=30)
    if not r.ok:
        try:
            details = r.json()
        except Exception:
            details = r.text
        raise RuntimeError(f"List wikis failed: {r.status_code} {r.reason} -> {details}")

    wikis = r.json().get("value", [])
    if wiki_name:
        for w in wikis:
            if w.get("name") == wiki_name:
                return w
        return None
    # If no name provided, prefer a project wiki
    for w in wikis:
        if (w.get("type") or "").lower() == "projectwiki":
            return w
    return wikis[0] if wikis else None


def list_all_paths(org_url: str, project: str, wiki_id: str, headers: dict) -> Iterable[str]:
    """
    使用 recursionLevel=Full 取得所有頁面的路徑，回傳可迭代的頁面路徑集合。
    """
    url = f"{org_url}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
    params = {"recursionLevel": "Full", "api-version": API_VERSION}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if not r.ok:
        try:
            details = r.json()
        except Exception:
            details = r.text
        raise RuntimeError(f"List pages failed: {r.status_code} {r.reason} -> {details}")

    data = r.json() or {}
    # 支援兩種回傳結構：'value' 底下的清單，或含 'subPages' 的樹狀節點
    value = data.get("value")
    if isinstance(value, list):
        for item in value:
            p = item.get("path")
            if isinstance(p, str):
                yield p
        return

    # 樹狀結構
    def walk(node: dict):
        p = node.get("path")
        if isinstance(p, str):
            yield p
        for child in node.get("subPages", []) or []:
            if isinstance(child, dict):
                yield from walk(child)

    yield from walk(data)


def get_page_content(org_url: str, project: str, wiki_id: str, path: str, headers: dict) -> Optional[str]:
    url = f"{org_url}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
    params = {"path": path, "includeContent": "true", "api-version": API_VERSION}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if r.status_code == 200:
        try:
            return r.json().get("content")
        except Exception:
            return None
    # 若路徑為容器且沒有內容，可能出現 404
    return None


def path_to_local_file(output_root: Path, wiki_path: str) -> Path:
    # Wiki 路徑範例："/Home"、"/Networking"、"/Networking/Sub Page"
    parts = [seg for seg in wiki_path.strip().split('/') if seg]
    if not parts:
        parts = ["Home"]
    safe_parts = [sanitize_segment(p) for p in parts]
    if len(safe_parts) == 1:
        # 根目錄層級的頁面直接輸出在根目錄
        return output_root / f"{safe_parts[0]}.md"
    # 巢狀：最後一段為檔名，其餘為子資料夾
    *dirs, leaf = safe_parts
    return output_root.joinpath(*dirs) / f"{leaf}.md"


def export_wiki(org_url: str, project: str, wiki_name: Optional[str], output_dir: Path, headers: dict) -> int:
    wiki = find_wiki(org_url, project, wiki_name, headers)
    if not wiki:
        print(f"ERROR: Wiki not found. Name={wiki_name!r}")
        return 2

    wiki_id = wiki.get("id")
    if not wiki_id:
        print("ERROR: Wiki id missing from descriptor.")
        return 2

    ensure_output_dir(output_dir)

    paths = list(dict.fromkeys(list_all_paths(org_url, project, wiki_id, headers)))  # 去重且保留順序
    if not paths:
        print("[i] No pages found to export.")
        return 0

    # 判斷容器頁面：若某路徑為其他路徑的前綴且後面接 '/'，則視為容器
    norm_paths = [p.strip().rstrip('/') for p in paths]
    path_set = set(norm_paths)
    containers: set[str] = set()
    for p in norm_paths:
        prefix = p + '/'
        for q in norm_paths:
            if q != p and q.startswith(prefix):
                containers.add(p)
                break

    exported = 0
    created_dirs = 0
    for p in paths:
        p_norm = p.strip().rstrip('/')
        parts = [seg for seg in p_norm.split('/') if seg]
        if not parts:
            parts = ["Home"]
        safe_parts = [sanitize_segment(seg) for seg in parts]

        is_container = p_norm in containers
        if is_container:
            # 依頁面路徑建立對應的資料夾
            dir_path = output_dir.joinpath(*safe_parts)
            dir_path.mkdir(parents=True, exist_ok=True)
            created_dirs += 1
            # 若容器頁面仍有內容，則以 index.md 儲存
            content = get_page_content(org_url, project, wiki_id, (p_norm or "/"), headers)
            if content is not None and content != "":
                index_file = dir_path / "index.md"
                index_file.write_text(content, encoding="utf-8")
                exported += 1
                print(f"[+] Exported: {p_norm} -> {index_file}")
            else:
                print(f"[i] Created folder for container page: {p_norm} -> {dir_path}")
        else:
            # 葉節點頁面：直接寫成上層目錄下的 .md 檔
            parent_dir = output_dir if len(safe_parts) == 1 else output_dir.joinpath(*safe_parts[:-1])
            parent_dir.mkdir(parents=True, exist_ok=True)
            content = get_page_content(org_url, project, wiki_id, (p_norm or "/"), headers)
            if content is None:
                # 無內容 — 略過檔案但仍確保目錄存在
                print(f"[i] Skipped empty leaf: {p_norm}")
                continue
            leaf_file = parent_dir / f"{safe_parts[-1]}.md"
            leaf_file.write_text(content, encoding="utf-8")
            exported += 1
            print(f"[+] Exported: {p_norm} -> {leaf_file}")

    print(f"[+] Done. Exported {exported} page(s) and created {created_dirs} folder(s) at {output_dir}")
    return 0


def main(argv: list[str]) -> int:
    # 從 repo 根目錄載入 .env
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    org_url = (env("AZDO_ORG_URL") or "").rstrip('/')
    project = env("AZDO_PROJECT")
    wiki_name = env("AZDO_WIKI")
    output_dir = Path(env("OUTPUT_DIR") or (repo_root / "wiki-export")).resolve()

    if not org_url or not project:
        print("ERROR: Missing AZDO_ORG_URL or AZDO_PROJECT in environment/.env")
        return 2

    pat = env("AZDO_PAT")
    if not pat:
        print("ERROR: Set AZDO_PAT environment variable with a valid Personal Access Token")
        return 2

    headers = {"Accept": "application/json"}
    headers.update(get_auth_header(pat))

    try:
        return export_wiki(org_url, project, wiki_name, output_dir, headers)
    except Exception as ex:
        print(f"ERROR: {ex}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
