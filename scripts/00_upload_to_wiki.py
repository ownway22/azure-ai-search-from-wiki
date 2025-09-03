r"""
將 IT-knowledge 子資料夾上傳為 Azure DevOps Wiki 頁面並建立子頁面。
- 建立（或重用）Project Wiki
- 針對每個子資料夾（Networking、Security、DevOps）建立/更新對應的頂層頁面
- 為子資料夾中的每個檔案建立/更新子頁面

驗證：透過環境變數 AZDO_PAT 提供 Personal Access Token (PAT)。
設定：從 .env 或系統環境讀取 AZDO_ORG_URL、AZDO_PROJECT、AZDO_WIKI（名稱）以及 IT_KNOWLEDGE_ROOT。

範例（PowerShell）：
    # 設定 PAT（或寫入 .env）
    $env:AZDO_PAT = "<your_pat>"
    python .\scripts\upload_it_knowledge_to_wiki.py

注意事項
- 使用 REST API 7.2。
- 具冪等性：若頁面已存在則更新，否則建立。
- 對 .md/.txt 以原始內容呈現；其他副檔名以圍欄程式碼區塊嵌入。
"""
from __future__ import annotations
import base64
import os
import sys
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

API_VERSION = "7.2-preview"

SUPPORTED_TEXT_EXTS = {".md", ".txt"}
CODE_BLOCK_EXTS = {
    ".py": "python",
    ".ps1": "powershell",
    ".sh": "bash",
    ".js": "javascript",
    ".ts": "typescript",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".xml": "xml",
    ".cs": "csharp",
}


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v else default


def get_auth_header(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def ensure_wiki(org_url: str, project: str, wiki_name: str, headers: dict) -> dict:
    # 列出現有的 wikis
    list_url = f"{org_url}/{project}/_apis/wiki/wikis"
    r = requests.get(list_url, headers=headers, params={"api-version": API_VERSION}, timeout=30)
    if not r.ok:
        print(f"[Azure DevOps] List wikis failed: {r.status_code} {r.reason} -> {list_url}")
        try:
            print("Response JSON:", r.json())
        except Exception:
            print("Response Text:", r.text[:2000])
        r.raise_for_status()
    for w in r.json().get("value", []):
        if w.get("name") == wiki_name:
            return w

    # 若不存在則建立 project wiki（類型為 ProjectWiki）
    create_url = f"{org_url}/{project}/_apis/wiki/wikis"
    payload = {"name": wiki_name, "type": "projectWiki"}
    r = requests.post(create_url, headers=headers, params={"api-version": API_VERSION}, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        r.raise_for_status()
    return r.json()


def get_page(org_url: str, project: str, wiki_id: str, path: str, headers: dict) -> tuple[int, Optional[str]]:
    url = f"{org_url}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
    r = requests.get(
        url,
        headers=headers,
        params={"path": path, "includeContent": "false", "api-version": API_VERSION},
        timeout=30,
    )
    if r.status_code == 200:
        return 200, r.headers.get("ETag")
    return r.status_code, None


def create_or_update_page(org_url: str, project: str, wiki_id: str, path: str, content: str, headers: dict, etag: Optional[str]) -> None:
    url = f"{org_url}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
    req_headers = {**headers, "Content-Type": "application/json"}
    params = {"path": path, "api-version": API_VERSION}
    payload = {"content": content}

    if etag:
        req_headers["If-Match"] = etag
        r = requests.patch(url, headers=req_headers, params=params, json=payload, timeout=30)
    else:
        r = requests.put(url, headers=req_headers, params=params, json=payload, timeout=30)

    if r.status_code not in (200, 201):
        if r.status_code == 409 and not etag:
            status, new_etag = get_page(org_url, project, wiki_id, path, headers)
            if status == 200 and new_etag:
                req_headers["If-Match"] = new_etag
                r = requests.patch(url, headers=req_headers, params=params, json=payload, timeout=30)
        r.raise_for_status()


def file_to_markdown(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        # 無法讀取（可能為二進位或權限不足）時改以提示文字回傳
        return f"> Unable to display file `{path.name}` (binary or unreadable)"

    if ext in SUPPORTED_TEXT_EXTS:
        return text

    lang = CODE_BLOCK_EXTS.get(ext, "")
    fence = f"```{lang}" if lang else "```"
    return f"{fence}\n{text}\n```"


def make_page_path(folder_name: str, relative_file: Optional[Path] = None) -> str:
    if not relative_file:
        return folder_name
    stem = relative_file.stem if relative_file.suffix.lower() in SUPPORTED_TEXT_EXTS else relative_file.name
    return f"{folder_name}/{stem}"


def upload_folder(org_url: str, project: str, wiki: dict, folder: Path, headers: dict) -> None:
    wiki_id = wiki["id"]
    folder_name = folder.name

    # 建立/更新此資料夾對應的頂層頁面，並包含索引
    page_path = make_page_path(folder_name)
    lines = [f"# {folder_name}", "", "Sub-pages:"]
    for p in sorted(folder.glob("*")):
        if p.is_file():
            sub_page_path = make_page_path(folder_name, p.relative_to(folder))
            display = p.stem if p.suffix.lower() in SUPPORTED_TEXT_EXTS else p.name
            lines.append(f"- [{display}]({sub_page_path})")
    index_md = "\n".join(lines)

    status, etag = get_page(org_url, project, wiki_id, page_path, headers)
    create_or_update_page(org_url, project, wiki_id, page_path, index_md, headers, etag if status == 200 else None)

    # 將資料夾內每個檔案上傳為子頁面
    for p in sorted(folder.glob("*")):
        if not p.is_file():
            continue
        sub_page_path = make_page_path(folder_name, p.relative_to(folder))
        content = file_to_markdown(p)
        status, etag = get_page(org_url, project, wiki_id, sub_page_path, headers)
        create_or_update_page(org_url, project, wiki_id, sub_page_path, content, headers, etag if status == 200 else None)


def main(argv: list[str]) -> int:
    # 從 repo 根目錄載入 .env（位於此腳本上一層）
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    org_url = env("AZDO_ORG_URL")
    project = env("AZDO_PROJECT")
    wiki_name = env("AZDO_WIKI") or "ProjectWiki"
    root_str = env("IT_KNOWLEDGE_ROOT") or str(repo_root / "IT-knowledge")

    if not org_url or not project:
        print("ERROR: Missing configuration. Set AZDO_ORG_URL and AZDO_PROJECT in .env or environment.")
        return 2

    pat = env("AZDO_PAT")
    if not pat:
        print("ERROR: Set AZDO_PAT environment variable with a valid Personal Access Token")
        return 2

    headers = {"Accept": "application/json"}
    headers.update(get_auth_header(pat))

    wiki = ensure_wiki(org_url.rstrip("/"), project, wiki_name, headers)

    root = Path(root_str)
    if not root.exists():
        print(f"ERROR: Root folder not found: {root}")
        return 2

    # 僅處理以下子資料夾
    wanted = {"Networking", "Security", "DevOps"}
    for sf in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        if sf.name in wanted:
            print(f"[+] Uploading folder: {sf}")
            upload_folder(org_url.rstrip("/"), project, wiki, sf, headers)

    print("[+] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
