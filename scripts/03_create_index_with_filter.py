"""
透過 REST 建立或更新 Azure AI Search index，範例結構參考 templates/sample_REST_call.txt。

結構欄位對應 templates/it_knowledge.json：
    - id（鍵值）
    - file_name（可篩選、可全文搜尋）
    - category（可篩選、可分面、可排序）
    - type（可篩選、可分面、可排序）
    - content（可全文搜尋）
並包含可選的向量欄位 `contentVector`（HNSW 設定，維度預設 1536）。

環境變數（由 .env 或 shell 提供）：
    SEARCH_SERVICE_NAME      Azure AI Search 服務名稱（必要）
    AI_SEARCH_KEY            具索引管理權限的 Admin 或 Query key（必要）
    INDEX_NAME               索引名稱（預設：it-knowledge-index）
    API_VERSION              API 版本（預設：2023-11-01）
    EMBEDDING_DIMENSIONS     向量維度（預設：1536）

使用範例（PowerShell）：
    # 建立（POST）。若索引已存在則失敗
    uv run python .\\scripts\\03_create_index_with_filter.py

    # 覆寫/更新（PUT）
    uv run python .\\scripts\\03_create_index_with_filter.py --overwrite

也可改用參數傳入，不透過環境變數：
    uv run python .\\scripts\\03_create_index_with_filter.py `
        --service-name mysearch `
        --api-key $env:AI_SEARCH_KEY `
        --index-name it-knowledge-index `
        --api-version 2023-11-01 `
        --embedding-dimensions 1536
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


# --- Env helpers -------------------------------------------------------------
def _env_value(name: str, default: Optional[str] = None) -> Optional[str]:
    """Get an env var and trim surrounding quotes/whitespace; return default if missing."""
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().strip('"').strip("'")
    return v if v != "" else default


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env_value(name)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "on", "y", "t"}


def _env_int(name: str, default: int) -> int:
    v = _env_value(name)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


def build_index_schema(index_name: str, embedding_dimensions: int) -> Dict[str, Any]:
    """組裝 index 結構描述（schema）。

    欄位對齊 it_knowledge.json，並額外加入向量欄位。
    """
    return {
        "name": index_name,
        "fields": [
            {  # Key（鍵值）
                "name": "id",
                "type": "Edm.String",
                "key": True,
                "filterable": False,
                "sortable": False,
                "facetable": False,
            },
            {  # 檔名（用於顯示/搜尋）
                "name": "file_name",
                "type": "Edm.String",
                "searchable": True,
                "filterable": True,
                "sortable": True,
                "facetable": False,
            },
            {  # 類別（用於篩選/分面）
                "name": "category",
                "type": "Edm.String",
                "searchable": False,
                "filterable": True,
                "sortable": True,
                "facetable": True,
            },
            {  # 類型（用於篩選/分面）
                "name": "type",
                "type": "Edm.String",
                "searchable": False,
                "filterable": True,
                "sortable": True,
                "facetable": True,
            },
            {  # 內容（可全文搜尋）
                "name": "content",
                "type": "Edm.String",
                "searchable": True,
                "filterable": False,
                "sortable": False,
                "facetable": False,
            },
            {  # 向量欄位（可用於 Hybrid/向量搜尋；選用）
                "name": "contentVector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "retrievable": True,
                "dimensions": int(embedding_dimensions),
                "vectorSearchProfile": "my-default-vector-profile",
            },
        ],
        "vectorSearch": {
            "algorithms": [
                {
                    "name": "my-hnsw-config-1",
                    "kind": "hnsw",
                    "hnswParameters": {
                        "m": 4,
                        "efConstruction": 400,
                        "efSearch": 500,
                        "metric": "cosine",
                    },
                }
            ],
            "profiles": [
                {
                    "name": "my-default-vector-profile",
                    "algorithm": "my-hnsw-config-1",
                }
            ],
        },
    }


def create_or_update_index(
    service_name: str,
    api_key: str,
    api_version: str,
    index_name: str,
    embedding_dimensions: int,
    overwrite: bool,
) -> requests.Response:
    base_url = f"https://{service_name}.search.windows.net"
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,
    }
    body = build_index_schema(index_name, embedding_dimensions)
    if overwrite:
        # PUT /indexes('{indexName}') 進行建立或更新
        url = f"{base_url}/indexes('{index_name}')?api-version={api_version}"
        method = requests.put
    else:
        # POST /indexes 建立（若已存在會回傳 409）
        url = f"{base_url}/indexes?api-version={api_version}"
        method = requests.post
    resp = method(url, headers=headers, data=json.dumps(body), timeout=30)
    return resp


def load_items_from_json(json_path: str) -> List[Dict[str, Any]]:
    """從 it_knowledge.json 載入 items 陣列。"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        raise ValueError("JSON 結構錯誤：缺少 items 陣列。")
    return items


def chunked(seq: List[Any], size: int) -> List[List[Any]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def upload_documents(
    service_name: str,
    api_key: str,
    api_version: str,
    index_name: str,
    docs: List[Dict[str, Any]],
    batch_size: int = 1000,
) -> Tuple[int, int]:
    """將文件以批次上傳至 Azure AI Search。

    回傳 (成功數, 失敗數)。
    """
    base_url = f"https://{service_name}.search.windows.net"
    url = f"{base_url}/indexes('{index_name}')/docs/index?api-version={api_version}"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "api-key": api_key,
    }

    success = 0
    failed = 0

    for batch in chunked(docs, batch_size):
        payload = {
            "value": [
                {
                    "@search.action": "upload",
                    # 僅上傳必要欄位；contentVector 可留空以後續補齊
                    "id": str(doc.get("id", "")),
                    "file_name": doc.get("file_name", ""),
                    "category": doc.get("category", ""),
                    "type": doc.get("type", ""),
                    "content": doc.get("content", ""),
                }
                for doc in batch
            ]
        }

        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        try:
            result = resp.json()
        except Exception:
            result = {"text": resp.text}

        if not resp.ok:
            print(f"[!] 文件批次上傳失敗（HTTP {resp.status_code}）：{result}")
            failed += len(batch)
            continue

        # 成功與失敗依回傳結果逐筆判斷
        for res in result.get("value", []):
            if res.get("status") is True:
                success += 1
            else:
                failed += 1

    return success, failed


def parse_args() -> argparse.Namespace:
    load_dotenv()  # Load from .env if present
    parser = argparse.ArgumentParser(description="建立或更新 Azure AI Search index（REST）")
    parser.add_argument("--service-name", default=_env_value("SEARCH_SERVICE_NAME"), help="Search 服務名稱（亦可用環境變數 SEARCH_SERVICE_NAME）")
    parser.add_argument("--api-key", default=_env_value("AI_SEARCH_KEY"), help="Search API key（亦可用環境變數 AI_SEARCH_KEY）")
    parser.add_argument("--index-name", default=_env_value("INDEX_NAME", "it-knowledge-index"), help="索引名稱（亦可用環境變數 INDEX_NAME）")
    parser.add_argument("--api-version", default=_env_value("API_VERSION", "2023-11-01"), help="API 版本（亦可用環境變數 API_VERSION）")
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=_env_int("EMBEDDING_DIMENSIONS", 1536),
        help="contentVector 的向量維度（預設：1536；亦可用環境變數 EMBEDDING_DIMENSIONS）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=_env_bool("OVERWRITE", False),
        help="若索引已存在，改用 PUT 覆寫/更新（亦可用環境變數 OVERWRITE=true 啟用）",
    )
    parser.add_argument(
        "--json-path",
        default=_env_value("SCHEMA_FILE") or os.path.join(os.path.dirname(__file__), "it_knowledge.json"),
        help="要上傳的 JSON 資料檔路徑（亦可用環境變數 SCHEMA_FILE；預設為此目錄 it_knowledge.json）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_env_int("BATCH_SIZE", 1000),
        help="批次上傳大小（預設：1000；亦可用環境變數 BATCH_SIZE）",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        default=_env_bool("SCHEMA_ONLY", False),
        help="僅建立/更新索引結構，不上傳文件（亦可用環境變數 SCHEMA_ONLY=true 啟用）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = [name for name, val in {"service-name": args.service_name, "api-key": args.api_key}.items() if not val]
    if missing:
        print(f"ERROR: 缺少必要參數：{', '.join(missing)}")
        return 2

    resp = create_or_update_index(
        service_name=args.service_name,
        api_key=args.api_key,
        api_version=args.api_version,
        index_name=args.index_name,
        embedding_dimensions=args.embedding_dimensions,
        overwrite=bool(args.overwrite),
    )

    try:
        payload = resp.json()
    except Exception:
        payload = {"text": resp.text}

    if resp.ok:
        action = "updated" if args.overwrite else "created"
        print(f"[+] Index '{args.index_name}' {action} successfully. Status: {resp.status_code}")
        print(json.dumps(payload, indent=2))
        # 若僅建立 schema，直接結束
        if args.schema_only:
            return 0

        # 讀取 JSON 並上傳至索引
        try:
            json_path = args.json_path
            if not json_path:
                raise ValueError("未提供 --json-path，且環境變數 SCHEMA_FILE 也未設定。")

            # Ensure path is usable even if quoted in .env and support relative paths.
            json_path = json_path.strip().strip('"').strip("'")
            if not os.path.isabs(json_path) and not os.path.exists(json_path):
                candidate = os.path.join(os.path.dirname(__file__), json_path)
                if os.path.exists(candidate):
                    json_path = candidate

            items = load_items_from_json(json_path)
            if not items:
                print("[i] 找不到可上傳的文件（items 為空）。")
                return 0

            success, failed = upload_documents(
                service_name=args.service_name,
                api_key=args.api_key,
                api_version=args.api_version,
                index_name=args.index_name,
                docs=items,
                batch_size=args.batch_size,
            )
            print(f"[+] 文件上傳完成：成功 {success} 筆，失敗 {failed} 筆。")
            return 0 if failed == 0 else 1
        except Exception as ex:
            print(f"[!] 上傳文件時發生錯誤：{ex}")
            return 1
    else:
        print(f"[!] 請求失敗（HTTP {resp.status_code}）")
        print(json.dumps(payload, indent=2))
        # 若 POST 衝突，提供提示
        if resp.status_code == 409 and not args.overwrite:
            print("提示：使用 --overwrite 以 PUT 更新既有索引。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
