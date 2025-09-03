# 🔎 azure-ai-search-from-wiki
以 Python 腳本將 Azure DevOps Wiki 與本機知識庫串接，支援 Markdown 匯出/上傳、JSON 彙整、建立含向量欄位的 Azure AI Search 索引，以及以 Azure AI Foundry Agents 產生 QA 測試集。協助您在 Azure DevOps 與本機知識庫間往返：
- 將 Azure DevOps Project Wiki 匯出為本機 Markdown 檔。
- 將本機 IT-knowledge 目錄上傳回 Azure DevOps Wiki（自動建立頁面與子頁面）。
- 彙整匯出的 Markdown 為結構化 JSON。
- 建立 Azure AI Search 索引（含向量欄位）。
- 透過 Azure AI Foundry Agents 依據內容自動產生測試用 QA 資料集（JSONL）。

適合想要快速把團隊 Wiki 內容導入搜尋與代理（agents）應用的開發者與資料工程師。

---

## 功能特色
- 匯出 Azure DevOps Project Wiki 至本機，保留資料夾與頁面階層。
- 將本機 IT-knowledge 目錄（Networking / Security / DevOps）一鍵上傳為 Wiki 頁面與子頁面。
- 將所有 Markdown 彙整成單一 `it_knowledge.json`（含類別與類型標註）。
- 透過 REST 建立 Azure AI Search 索引，支援全文與向量（Hybrid）搜尋。
- 使用 Azure AI Foundry Agents 自動產生測試集 `testset.jsonl`（問答急）。

---

## 系統需求
- Python：3.10 以上
- 推薦工具：
  - [uv](https://github.com/astral-sh/uv)（可選，用於依賴同步與鎖定）
  - 或使用內建 venv + pip

---

## 專案結構（摘要）
```
azure-ai-search-from-wiki/
├─ IT-knowledge/                 # 範例本機知識庫（Networking/Security/DevOps）
├─ wiki-export/                  # 匯出的 Wiki 內容（腳本 01 產出）
├─ scripts/
│  ├─ 00_upload_to_wiki.py       # 將 IT-knowledge 上傳成 Azure DevOps Wiki 頁面
│  ├─ 01_download_from_wiki.py   # 從 Azure DevOps Wiki 匯出成 .md
│  ├─ 02_create_json.py          # 匯總 wiki-export -> it_knowledge.json
│  ├─ 03_create_index_with_filter.py # 建立 Azure AI Search 索引（含向量欄位）
│  ├─ 05_create_testset.py       # 以 Azure AI Agents 產生測試集 testset.jsonl
│  ├─ it_knowledge.json          # 02 產生之 JSON（示例）
│  └─ testset.jsonl              # 05 產生之 JSONL（示例）
├─ pyproject.toml                # 專案依賴與設定
└─ README.md
```

---

## 安裝與環境準備

您可以選擇使用 uv 或一般 venv + pip。兩者擇一即可。

### 使用 uv（建議）
1) 安裝 uv（若尚未安裝）：
```powershell
pip install uv
```

2) 在專案根目錄同步依賴：
```powershell
uv sync
```

---

## 環境變數與 .env 設定

將必要的設定放在系統環境變數或專案根目錄的 `.env` 檔案。建議使用 `.env` 方便本機開發，但請勿將機敏金鑰提交到版本控制。

以下為 `.env` 範例（請自行以實際值取代 <> 區塊）：
```env
# Azure DevOps
AZDO_ORG_URL=https://dev.azure.com/<your-org>/
AZDO_PROJECT=<your-project-name>
AZDO_WIKI=<your-wiki-name>            # 例如 Code Wiki：MyProject.wiki
AZDO_PAT=<your-azdo-personal-access-token-with-wiki-read>
OUTPUT_DIR=./wiki-export               # 匯出目錄，預設為 ./wiki-export

# Azure AI Search
SEARCH_SERVICE_NAME=<your-search-service-name>
AI_SEARCH_KEY=<your-search-admin-or-query-key-with-index-rights>
API_VERSION=2023-11-01
INDEX_NAME=it-knowledge-index          # 若使用 03 腳本的預設值，可不填

# Azure AI Foundry / Projects（產生測試集用）
PROJECT_ENDPOINT=<your-ai-project-endpoint> # 例：https://...services.ai.azure.com/api/projects/<proj>
MODEL_DEPLOYMENT_NAME=<your-model-deployment-name> # 例：gpt-4o

# 05_create_testset.py 可選參數（皆為選填）
WIKI_EXPORT_DIR=./wiki-export
OUTPUT_JSONL=./scripts/testset.jsonl
QA_PER_SUBFOLDER=10
MAX_FILE_SIZE_MB=25
VECTORSTORE_EXPIRE_DAYS=7
```

---

## 使用步驟

以下各步驟對應 `scripts/` 目錄內的腳本，可依需求單獨或串接使用。

### 1) 從 Azure DevOps Wiki 匯出為本機 Markdown（01_download_from_wiki.py）
將指定的 Project Wiki 匯出到 `OUTPUT_DIR`（預設 `./wiki-export`），保留頁面階層：
```powershell
uv run python .\scripts\01_download_from_wiki.py
```
匯出後，容器頁面若同時擁有內容，會儲存為 `index.md`，其子頁面則成為子資料夾內的獨立檔案。

### 2) 將本機 IT-knowledge 上傳為 Azure DevOps Wiki 頁面（00_upload_to_wiki.py）
把 `IT-knowledge/` 底下的 `Networking`、`Security`、`DevOps` 子資料夾，上傳至 Azure DevOps Wiki（頂層頁 + 子頁面）。同名頁面會被更新（冪等）：
```powershell
uv run python .\scripts\00_upload_to_wiki.py
```
可透過 `IT_KNOWLEDGE_ROOT`（.env 或環境變數）調整來源根目錄，預設為專案根目錄下的 `IT-knowledge`。

### 3) 產生彙整 JSON（02_create_json.py）
讀取 `wiki-export/` 底下所有 `.md`，排除 `Home.md` 與 `index.md`，輸出 `scripts/it_knowledge.json`。同時以目錄與關鍵字啟發式推斷：
- `category`：Networking / Security / DevOps
- `type`：code / meeting_notes / knowledge / credentials / others
```powershell
uv run python .\scripts\02_create_json.py
```

### 4) 建立 Azure AI Search 索引（03_create_index_with_filter.py）
使用 REST 建立（或以 `--overwrite` 更新）索引，包含下列欄位：
`id`（key）、`file_name`、`category`、`type`、`content`、`contentVector`（向量欄位，預設 1536 維，HNSW）：
```powershell
uv run python .\scripts\03_create_index_with_filter.py
```
需事先設定 `SEARCH_SERVICE_NAME` 與 `AI_SEARCH_KEY`（具索引管理權限）。

> 提示：本腳本負責建立結構（schema）。若需將 `it_knowledge.json` 內容批次上傳至索引，可另行撰寫上傳腳本（Index Documents API）。

### 5) 產生測試集（05_create_testset.py）
針對 `wiki-export/` 下每個子資料夾：
1. 上傳所有檔案到 Agents Files API。
2. 建立一個 vector store 並透過 `FileSearchTool` 掛載到一個 `gpt-4o`（或您指定部署）agent。
3. 以系統提示要求 agent 僅根據文件內容產生固定數量（預設 10 組）的問答對。
4. 聚合輸出為 `scripts/testset.jsonl`（每行 `{ "query": "...", "ground_truth": "..." }`）。

執行：
```powershell
uv run python .\scripts\05_create_testset.py
```
必要環境：
- `PROJECT_ENDPOINT`：Azure AI Project Endpoint。
- `MODEL_DEPLOYMENT_NAME`：在該 Project 已部署的模型名稱（例：`gpt-4o`）。
- 已登入 Azure（`DefaultAzureCredential` 可透過 CLI / VS / Managed Identity 等方式取得權杖）。

可選環境：
- `WIKI_EXPORT_DIR`、`OUTPUT_JSONL`、`QA_PER_SUBFOLDER`、`MAX_FILE_SIZE_MB`、`VECTORSTORE_EXPIRE_DAYS`。

---