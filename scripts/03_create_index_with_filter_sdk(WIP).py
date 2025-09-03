"""
使用 Azure SDK (azure-search-documents) 建立/更新 Azure AI Search Index，並上傳 JSON 文件。

功能對齊 scripts/03_create_index_with_filter.py，差異：
- 以 SDK 定義 Index，加入 AzureOpenAIVectorizer（向量器）以在上傳時自動產生 contentVector。
- 以 SearchIndexingBufferedSender 進行文件批次上傳。

環境變數（.env）：
- SEARCH_SERVICE_NAME            服務名稱（必要）
- AI_SEARCH_KEY                  Admin/Query Key（必要，建議用 Admin）
- INDEX_NAME                     索引名稱（預設：it-knowledge-index）
- EMBEDDING_DIMENSIONS           向量維度（預設：1536）
- SCHEMA_FILE                    JSON 檔路徑（預設：scripts/it_knowledge.json）
- OVERWRITE                      true/false（預設：false；true 則強制更新）
- BATCH_SIZE                     批次上傳大小（預設：1000）

Azure OpenAI（用於 vectorizers）：
- AZURE_OPENAI_ENDPOINT                  例：https://<resource>.openai.azure.com/
- AZURE_OPENAI_EMBEDDING_DEPLOYMENT      例：text-embedding-3-small（部署名稱）
- AZURE_OPENAI_EMBEDDING_MODEL           例：text-embedding-3-small（模型名）

注意：若缺少 AZURE_OPENAI_EMBEDDING_* 設定，將無法建立包含 vectorizers 的 Index。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
import random
import string

from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceExistsError, HttpResponseError
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
)
from azure.search.documents import SearchIndexingBufferedSender, SearchClient


# --- 環境變數輔助函式 --------------------------------------------------------
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


def _env_int(name: str, default: int) -> int:
    v = _env_value(name)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _load_items(json_path: str) -> List[Dict[str, Any]]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        raise ValueError("JSON 結構錯誤：缺少 items 陣列。")
    return items


def main() -> int:
    # 載入 .env
    if not load_dotenv():
        load_dotenv(override=True)

    service_name = _env_value("SEARCH_SERVICE_NAME")
    api_key = _env_value("AI_SEARCH_KEY")
    index_name = _env_value("INDEX_NAME", "it-knowledge-index")
    embedding_dims = _env_int("EMBEDDING_DIMENSIONS", 1536)
    overwrite = _env_bool("OVERWRITE", False)
    batch_size = _env_int("BATCH_SIZE", 1000)

    # 解析 JSON 路徑
    json_path = _env_value("SCHEMA_FILE") or os.path.join(os.path.dirname(__file__), "it_knowledge.json")
    json_path = (json_path or "").strip().strip('"').strip("'")
    if not os.path.isabs(json_path) and not os.path.exists(json_path):
        candidate = os.path.join(os.path.dirname(__file__), json_path)
        if os.path.exists(candidate):
            json_path = candidate

    # Azure OpenAI 向量器設定
    # 若環境變數值包含 .../openai/deployments/...，將其截斷為服務根 URL
    aoai_endpoint_raw = _env_value("AZURE_OPENAI_ENDPOINT")
    aoai_endpoint = None
    if aoai_endpoint_raw:
        parts = aoai_endpoint_raw.split("openai/deployments")[0]
        aoai_endpoint = parts if parts.endswith("/") else parts + "/"
    aoai_embedding_deployment = _env_value("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    aoai_embedding_model = _env_value("AZURE_OPENAI_EMBEDDING_MODEL")

    missing = [n for n, v in {"SEARCH_SERVICE_NAME": service_name, "AI_SEARCH_KEY": api_key}.items() if not v]
    if missing:
        print(f"ERROR: 缺少必要參數：{', '.join(missing)}")
        return 2

    if not (aoai_endpoint and aoai_embedding_deployment and aoai_embedding_model):
        print("ERROR: 缺少 Azure OpenAI 向量器設定（AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_EMBEDDING_DEPLOYMENT / AZURE_OPENAI_EMBEDDING_MODEL）。")
        return 2

    endpoint = f"https://{service_name}.search.windows.net"
    cred = AzureKeyCredential(api_key)

    # 產生隨機 3 個小寫英文字母後綴，避免索引名稱衝突。
    # Azure AI Search 的索引名稱只允許小寫字母、數字或連字號（-），因此使用 '-' 作為分隔符號。
    suffix = ''.join(random.choice(string.ascii_lowercase) for _ in range(3))
    indexed_name_with_suffix = f"{index_name}-{suffix}"

    print(f"[i] 實際建立/更新的索引名稱：{indexed_name_with_suffix}")

    # 定義索引欄位與 VectorSearch（包含 AzureOpenAIVectorizer）
    fields = [
        # id（鍵值）
        SearchField(name="id", type="Edm.String", key=True, filterable=False, sortable=False, facetable=False),
        # file_name（可搜尋/篩選/排序）
        SearchField(name="file_name", type="Edm.String", searchable=True, filterable=True, sortable=True, facetable=False),
        # category（可篩選/分面/排序）
        SearchField(name="category", type="Edm.String", searchable=False, filterable=True, sortable=True, facetable=True),
        # type（可篩選/分面/排序）
        SearchField(name="type", type="Edm.String", searchable=False, filterable=True, sortable=True, facetable=True),
        # content（全文搜尋）
        SearchField(name="content", type="Edm.String", searchable=True, filterable=False, sortable=False, facetable=False),
        # 向量欄位（對應 vector profile）
        SearchField(name="contentVector", type="Collection(Edm.Single)", vector_search_dimensions=embedding_dims, vector_search_profile_name="hnsw_openai"),
    ]

    vector_search = VectorSearch(
        profiles=[VectorSearchProfile(name="hnsw_openai", algorithm_configuration_name="alg", vectorizer_name="azure_openai_vec")],
        algorithms=[HnswAlgorithmConfiguration(name="alg")],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="azure_openai_vec",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=aoai_endpoint,
                    deployment_name=aoai_embedding_deployment,
                    model_name=aoai_embedding_model,
                ),
            )
        ],
    )

    index = SearchIndex(
        name=indexed_name_with_suffix,
        fields=fields,
        vector_search=vector_search,
    )

    index_client = SearchIndexClient(endpoint=endpoint, credential=cred)

    # 建立或更新索引
    try:
        if overwrite:
            index_client.create_or_update_index(index)
            print(f"[+] Index '{indexed_name_with_suffix}' created/updated (overwrite=true)")
        else:
            index_client.create_index(index)
            print(f"[+] Index '{indexed_name_with_suffix}' created")
    except ResourceExistsError:
        print(f"[!] Index '{indexed_name_with_suffix}' already exists. 設定 OVERWRITE=true 以更新。")
        return 1
    except HttpResponseError as e:
        print(f"[!] 建立/更新索引失敗：{e}")
        return 1

    # 上傳文件
    try:
        items = _load_items(json_path)
        if not items:
            print("[i] 找不到可上傳的文件（items 為空）。")
            return 0

        # 使用 SearchIndexingBufferedSender 上傳；會依索引上設定的 AzureOpenAIVectorizer 自動產生向量
        uploaded = 0
        with SearchIndexingBufferedSender(endpoint=endpoint, index_name=indexed_name_with_suffix, credential=cred, auto_flush=True) as sender:
            # 分批上傳
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                sender.upload_documents(documents=batch)
                uploaded += len(batch)
        print(f"[+] 文件上傳完成：成功 {uploaded} 筆。")

        # --- 後置驗證：確認索引已成功建立且可查詢 ---------------------------------
        try:
            # 讀回索引定義，確認存在
            idx = index_client.get_index(indexed_name_with_suffix)
            if not idx or not getattr(idx, "name", None):
                print(f"[!] 驗證失敗：無法讀回索引 '{indexed_name_with_suffix}'。")
            else:
                # 使用 SearchClient 查詢文件數量，驗證可用性
                search_client = SearchClient(endpoint=endpoint, index_name=indexed_name_with_suffix, credential=cred)
                doc_count = search_client.get_document_count()
                print(f"[✓] 驗證成功：索引存在且可查詢，文件數量 = {doc_count}。")
                if doc_count < uploaded:
                    print("[i] 注意：剛上傳後文件計數可能因最終一致性而略有延遲，屬正常現象。")
        except Exception as verify_ex:
            print(f"[!] 驗證索引狀態時發生錯誤：{verify_ex}")

        return 0
    except Exception as ex:
        print(f"[!] 上傳文件時發生錯誤：{ex}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
