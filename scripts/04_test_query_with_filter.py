"""
以 REST 測試 Azure AI Search 的查詢與過濾（Filter），可選擇純文字查詢或向量查詢。

預設從 .env 讀取設定：
  - SEARCH_SERVICE_NAME, AI_SEARCH_KEY, INDEX_NAME, API_VERSION
  - 可選 FILTER（原生 OData 條件字串），或使用 --category/--type 組合
  - 可選 USE_VECTOR=true 啟用向量查詢（需提供查詢向量）
  - 如需自動產生向量，可設定 AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_EMBEDDING_DEPLOYMENT

注意：若索引中文件尚未寫入 contentVector（無向量），請使用純文字查詢（--use-vector 不要開啟）。
參考文件：https://learn.microsoft.com/azure/search/vector-search-filters
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


# --- Env helpers -------------------------------------------------------------
def _env_value(name: str, default: Optional[str] = None) -> Optional[str]:
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


def build_filter(category: Optional[str], type_: Optional[str], raw: Optional[str]) -> Optional[str]:
    if raw:
        return raw
    parts = []
    if category:
        parts.append(f"category eq '{category}'")
    if type_:
        parts.append(f"type eq '{type_}'")
    return " and ".join(parts) if parts else None


def get_embedding(query_text: str) -> Optional[List[float]]:
    """可選：透過 Azure OpenAI 取得 embedding（需 .env 提供金鑰與部署名）。"""
    endpoint = _env_value("AZURE_OPENAI_ENDPOINT")
    api_key = _env_value("AZURE_OPENAI_API_KEY") or _env_value("AZURE_OPENAI_KEY")
    deployment = _env_value("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    if not (endpoint and api_key and deployment):
        return None

    url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/embeddings?api-version=2023-05-15"
    headers = {"Content-Type": "application/json", "api-key": api_key}
    body = {"input": query_text}
    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["embedding"]


def search_with_text(service: str, key: str, index: str, api_version: str, query_text: str, filter_expr: Optional[str], top: int) -> Dict[str, Any]:
    base = f"https://{service}.search.windows.net"
    url = f"{base}/indexes('{index}')/docs/search?api-version={api_version}"
    headers = {"Content-Type": "application/json; charset=utf-8", "api-key": key}
    body: Dict[str, Any] = {
        "search": query_text,
        "top": top,
        "select": "id,file_name,category,type",
    }
    if filter_expr:
        body["filter"] = filter_expr
    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    return {"ok": resp.ok, "status": resp.status_code, "json": _safe_json(resp)}


def search_with_vector(service: str, key: str, index: str, api_version: str, vector: List[float], filter_expr: Optional[str], top: int) -> Dict[str, Any]:
    base = f"https://{service}.search.windows.net"
    url = f"{base}/indexes('{index}')/docs/search?api-version={api_version}"
    headers = {"Content-Type": "application/json; charset=utf-8", "api-key": key}
    body: Dict[str, Any] = {
        "vectors": [
            {"value": vector, "fields": "contentVector", "k": top}
        ],
        "select": "id,file_name,category,type",
    }
    if filter_expr:
        body["filter"] = filter_expr
    resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    return {"ok": resp.ok, "status": resp.status_code, "json": _safe_json(resp)}


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text}


def parse_args() -> argparse.Namespace:
    load_dotenv()
    p = argparse.ArgumentParser(description="測試 Azure AI Search 查詢與 Filter")
    p.add_argument("--service-name", default=_env_value("SEARCH_SERVICE_NAME"))
    p.add_argument("--api-key", default=_env_value("AI_SEARCH_KEY"))
    p.add_argument("--index-name", default=_env_value("INDEX_NAME", "it-knowledge-index"))
    p.add_argument("--api-version", default=_env_value("API_VERSION", "2023-11-01"))
    p.add_argument("--query-text", default=_env_value("QUERY_TEXT", "vpn"))
    p.add_argument("--category", default=_env_value("CATEGORY"))
    p.add_argument("--type", dest="type_", default=_env_value("TYPE"))
    p.add_argument("--filter", dest="raw_filter", default=_env_value("FILTER"))
    p.add_argument("--top", type=int, default=int(_env_value("TOP", "5")))
    p.add_argument("--use-vector", action="store_true", default=_env_bool("USE_VECTOR", False))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    missing = [n for n, v in {"service-name": args.service_name, "api-key": args.api_key}.items() if not v]
    if missing:
        print(f"ERROR: 缺少必要參數：{', '.join(missing)}")
        return 2

    filter_expr = build_filter(args.category, args.type_, args.raw_filter)

    if args.use_vector:
        emb = get_embedding(args.query_text)
        if not emb:
            print("[i] 無法取得 embedding，改用純文字查詢。請在 .env 設定 AZURE_OPENAI_* 以啟用向量查詢。")
            result = search_with_text(args.service_name, args.api_key, args.index_name, args.api_version, args.query_text, filter_expr, args.top)
        else:
            result = search_with_vector(args.service_name, args.api_key, args.index_name, args.api_version, emb, filter_expr, args.top)
    else:
        result = search_with_text(args.service_name, args.api_key, args.index_name, args.api_version, args.query_text, filter_expr, args.top)

    ok = result["ok"]
    status = result["status"]
    payload = result["json"]
    if ok:
        hits = payload.get("value", [])
        print(f"[+] 查詢成功（HTTP {status}）。共 {len(hits)} 筆結果。")
        for i, doc in enumerate(hits, 1):
            print(f"  {i}. id={doc.get('id')} | file={doc.get('file_name')} | category={doc.get('category')} | type={doc.get('type')} | score={doc.get('@search.score')}")
        return 0
    else:
        print(f"[!] 查詢失敗（HTTP {status}）")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
