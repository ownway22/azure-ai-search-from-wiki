"""
使用 Azure AI Foundry Agents 產生測試資料集（testset.jsonl）。

本腳本將執行：
- 針對 ../wiki-export 底下每個子資料夾，將所有檔案上傳至 Agents Files API。
- 每個子資料夾建立一個 vector store，並透過 FileSearchTool 掛載到 gpt-4o agent。
- 提示 agent 僅根據已上傳檔案的內容，精確產生 10 組 QA pairs。
- 聚合所有子資料夾的 QA pair，輸出為 scripts/testset.jsonl（JSONL 格式，欄位為 {query, ground_truth}）。

環境需求：
- 必須設定 PROJECT_ENDPOINT（例如：https://<your-project>.dev.azure.com 或 Azure AI Project endpoint）
- 透過 DefaultAzureCredential 登入 Azure（Managed Identity、Visual Studio、Azure CLI 等）。

說明：
- 使用 DefaultAzureCredential（程式碼不包含機密）。
- 加入基本重試與更穩健的 JSON 解析。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from dotenv import load_dotenv

# Azure AI Agents SDKs（保留英文專有名詞）
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import FilePurpose, FileSearchTool, ListSortOrder


# ---------------------------
# 設定
# ---------------------------
load_dotenv(override=True)

WIKI_EXPORT_DIR = Path(
	os.environ.get("WIKI_EXPORT_DIR", Path(__file__).resolve().parents[1] / "wiki-export")
)
OUTPUT_JSONL = Path(
	os.environ.get("OUTPUT_JSONL", Path(__file__).resolve().parent / "testset.jsonl")
)

# Agent/model 設定
# 重要：請使用 Azure AI Foundry 專案中模型的部署名稱（deployment name）。
MODEL_DEPLOYMENT_NAME = os.environ.get("MODEL_DEPLOYMENT_NAME")
QA_PER_SUBFOLDER = int(os.environ.get("QA_PER_SUBFOLDER", "10"))

# 檔案上傳限制（避免誤上傳過大的二進位）
MAX_FILE_SIZE_MB = float(os.environ.get("MAX_FILE_SIZE_MB", "25"))  # 預設 25 MB

# Vector store 成本控管：自上次使用後 N 天自動過期
VECTORSTORE_EXPIRE_DAYS = int(os.environ.get("VECTORSTORE_EXPIRE_DAYS", "7"))

# 簡易重試設定（因應暫時性錯誤）
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0


@dataclass
class QAPair:
	query: str
	ground_truth: str


def _iter_subfolders(root: Path) -> Iterable[Path]:
	for p in sorted(root.iterdir()):
		if p.is_dir():
			yield p


def _iter_files(folder: Path) -> Iterable[Path]:
	# 包含所有一般檔案；依需求處理每個子資料夾的所有檔案
	for p in sorted(folder.rglob("*")):
		if p.is_file():
			yield p


def retryable(fn, *args, **kwargs):
	last_exc = None
	for attempt in range(1, MAX_RETRIES + 1):
		try:
			return fn(*args, **kwargs)
		except Exception as e:  # noqa: BLE001 – broad to provide resilience
			last_exc = e
			sleep_for = RETRY_BACKOFF_SEC * attempt
			print(f"Warning: attempt {attempt} failed: {e}. Retrying in {sleep_for:.1f}s...")
			time.sleep(sleep_for)
	if last_exc:
		raise last_exc


def _file_size_mb(path: Path) -> float:
	try:
		return path.stat().st_size / (1024 * 1024)
	except Exception:
		return 0.0


def upload_files_and_create_vector_store(agents_client, folder: Path) -> Tuple[str, List[str]]:
	"""Uploads all files in folder and returns (vector_store_id, file_ids)."""
	file_ids: List[str] = []
	for f in _iter_files(folder):
	# 預設略過過大的檔案
		size_mb = _file_size_mb(f)
		if MAX_FILE_SIZE_MB > 0 and size_mb > MAX_FILE_SIZE_MB:
			print(f"Skipping (>{MAX_FILE_SIZE_MB}MB): {f} ({size_mb:.2f} MB)")
			continue
		try:
			print(f"Uploading: {f}")
			uploaded = retryable(
				agents_client.files.upload_and_poll,
				file_path=str(f),
				purpose=FilePurpose.AGENTS,
			)
			file_ids.append(uploaded.id)
		except Exception as e:
			print(f"Error uploading {f}: {e}. Skipping.")

	if not file_ids:
		raise RuntimeError(f"No files uploaded from folder: {folder}")

	print(f"Creating vector store for {folder.name} with {len(file_ids)} files (expire {VECTORSTORE_EXPIRE_DAYS}d after last active)...")
	vector_store = retryable(
		agents_client.vector_stores.create_and_poll,
		file_ids=file_ids,
		name=f"wiki_export_{folder.name}",
		expires_after={
			"anchor": "last_active_at",
			"days": VECTORSTORE_EXPIRE_DAYS,
		},
	)
	return vector_store.id, file_ids


def create_agent_with_file_search(agents_client, vector_store_id: str, name: str):
	file_search = FileSearchTool(vector_store_ids=[vector_store_id])
	print(f"Creating agent '{name}' with model deployment '{MODEL_DEPLOYMENT_NAME}' and file search tool...")
	agent = retryable(
		agents_client.create_agent,
		model=MODEL_DEPLOYMENT_NAME,
		name=name,
		description="Wiki-grounded QA generator",
		tools=file_search.definitions,
		tool_resources=file_search.resources,
		instructions=(
			"你是一位專注於文件理解與問答資料集產生的助手。"
			"請嚴格根據我提供的知識庫(檔案向量索引)內容，產生正確且可驗證的問答資料。"
			"絕對不要臆測或使用外部知識。若文件沒有提到，就不要包含在答案中。"
		),
	)
	return agent


def build_prompt(qty: int) -> str:
	return (
		"請依據我提供的知識庫內容，產生恰好 {qty} 組問答資料。".format(qty=qty)
		+ "\n要求：\n"
		+ "1) 僅能根據知識庫內容作答，不可使用外部或常識補全。\n"
		+ "2) 每組包含 query 與 ground_truth 兩個欄位。\n"
		+ "3) 請以 JSON 陣列輸出，格式如下：\n"
		+ "   [ {\"query\": \"問題1\", \"ground_truth\": \"答案1\"}, ... ]\n"
		+ "4) 只輸出純 JSON，勿加入任何說明文字或 Markdown。\n"
		+ "5) 問題多樣化、具體，答案務必能從知識庫文件中找到依據。\n"
	)


def run_agent_and_get_messages(agents_client, agent_id: str, prompt: str):
	"""建立 thread、傳送使用者訊息、執行 agent，並回傳 thread 的訊息列表。

	使用與 azure-ai-agents 1.1.0b2 相容的高階方法。
	"""
	# 建立新 thread
	# 註：在此 SDK 版本中，threads 透過子用戶端建立；message 與 run 提供上層便捷方法。
	thread = retryable(agents_client.threads.create)

	# 將使用者訊息加入 thread
	retryable(
		agents_client.messages.create,
		thread_id=thread.id,
		role="user",
		content=prompt,
	)

	# 啟動一次 run 以在該 thread 上執行 agent
	run = retryable(
		agents_client.runs.create,
		thread_id=thread.id,
		agent_id=agent_id,
	)

	# 輪詢直到 run 完成
	for _ in range(60):  # 約 2 分鐘上限（60 * 2s）
		run = retryable(
			agents_client.runs.get,
			thread_id=thread.id,
			run_id=getattr(run, "id", None) or run.get("id"),
		)
		status = getattr(run, "status", None) or (run.get("status") if isinstance(run, dict) else None)
		if status in ("completed", "failed", "cancelled", "expired"):
			break
		time.sleep(2)

	# 以遞增排序取得訊息，確保最後取得 assistant 的回覆
	messages = retryable(
		agents_client.messages.list,
		thread_id=thread.id,
		order=ListSortOrder.ASCENDING,
	)
	return messages


def extract_text_from_message_content(content: Union[str, list, dict, object]) -> str:
	"""嘗試將訊息內容正規化為字串。"""
	# 部分 SDK 可能回傳內容片段的清單；嘗試合併
	if isinstance(content, str):
		return content
	try:
		# 若內容為結構化片段（例如具 .text 屬性）
		parts = []
		for part in content or []:
			# part 可能為 dict 或物件
			text_val: Optional[str] = None
			if hasattr(part, "text") and getattr(part, "text"):
				text_obj = getattr(part, "text")
				# 有些 SDK 會將值包在 .value 底下
				text_val = getattr(text_obj, "value", None) if hasattr(text_obj, "value") else str(text_obj)
			elif isinstance(part, dict) and "text" in part:
				text_field = part.get("text")
				if isinstance(text_field, dict) and "value" in text_field:
					text_val = text_field.get("value")
				else:
					text_val = str(text_field)
			if text_val:
				parts.append(text_val)
		if parts:
			return "\n".join(parts)
	except Exception:
		pass
	return str(content)


def extract_json_array(text: str) -> Optional[list]:
	"""從文字中擷取第一個 JSON 陣列，能容忍 code fence。"""
	if not text:
		return None
	# 若存在 code fence，先移除
	text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)

	# 先嘗試直接解析 JSON
	try:
		data = json.loads(text)
		if isinstance(data, list):
			return data
	except Exception:
		pass

	# 後援：尋找最外層的 [...]
	start = text.find("[")
	end = text.rfind("]")
	if start != -1 and end != -1 and end > start:
		candidate = text[start : end + 1]
		try:
			data = json.loads(candidate)
			if isinstance(data, list):
				return data
		except Exception:
			pass
	return None


def messages_to_qa_pairs(messages) -> List[QAPair]:
	last_assistant_text = None
	# 走訪訊息以取得最後的 assistant 回覆
	for m in messages:
		try:
			role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
			if role == "assistant":
				content = getattr(m, "content", None)
				if content is None and isinstance(m, dict):
					content = m.get("content")
				text = extract_text_from_message_content(content)
				if text:
					last_assistant_text = text
		except Exception:
			continue

	if not last_assistant_text:
		raise RuntimeError("No assistant message found to parse.")

	data = extract_json_array(last_assistant_text)
	if data is None:
		raise ValueError("Assistant output did not contain a valid JSON array.")

	qa_pairs: List[QAPair] = []
	for idx, item in enumerate(data, start=1):
		if not isinstance(item, dict):
			print(f"Skipping non-dict item at index {idx}.")
			continue
		q = str(item.get("query", "")).strip()
		a = str(item.get("ground_truth", "")).strip()
		if not q or not a:
			print(f"Skipping item missing fields at index {idx}: {item}")
			continue
		qa_pairs.append(QAPair(query=q, ground_truth=a))
	return qa_pairs


def write_jsonl(pairs: List[QAPair], out_path: Path) -> None:
	out_path.parent.mkdir(parents=True, exist_ok=True)
	with out_path.open("w", encoding="utf-8") as f:
		for p in pairs:
			f.write(json.dumps({"query": p.query, "ground_truth": p.ground_truth}, ensure_ascii=False))
			f.write("\n")
	print(f"Wrote {len(pairs)} items to {out_path}")


def main():
	load_dotenv()
	endpoint = os.environ.get("PROJECT_ENDPOINT")
	if not endpoint:
		print("Error: PROJECT_ENDPOINT environment variable is not set.")
		sys.exit(1)

	if not MODEL_DEPLOYMENT_NAME:
		print(
			"Error: MODEL_DEPLOYMENT_NAME environment variable is not set. "
			"Set it to the deployment name of your model in the Azure AI Foundry project."
		)
		sys.exit(1)

	credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
	project_client = AIProjectClient(credential=credential, endpoint=endpoint)

	all_pairs: List[QAPair] = []

	with project_client:
		agents_client = project_client.agents

		if not WIKI_EXPORT_DIR.exists():
			print(f"Error: wiki-export directory not found at {WIKI_EXPORT_DIR}")
			sys.exit(1)

		for subfolder in _iter_subfolders(WIKI_EXPORT_DIR):
			print("-" * 80)
			print(f"Processing subfolder: {subfolder}")
			agent = None
			vector_store_id = None
			try:
				vector_store_id, _ = upload_files_and_create_vector_store(agents_client, subfolder)
				agent = create_agent_with_file_search(
					agents_client, vector_store_id, name=f"QA_Generator_{subfolder.name}"
				)

				prompt = build_prompt(QA_PER_SUBFOLDER)
				messages = run_agent_and_get_messages(agents_client, agent.id, prompt)
				pairs = messages_to_qa_pairs(messages)

				# Enforce exact count when possible
				if len(pairs) != QA_PER_SUBFOLDER:
					print(
						f"Warning: expected {QA_PER_SUBFOLDER} pairs, got {len(pairs)} from {subfolder.name}. Using what was returned."
					)
				all_pairs.extend(pairs)
				print(f"Collected {len(pairs)} QA pairs from {subfolder.name}.")
			except Exception as e:
				print(f"Error processing {subfolder.name}: {e}")
			finally:
				# Best-effort cleanup to manage costs
				try:
					if agent is not None and getattr(agent, 'id', None):
						retryable(agents_client.delete_agent, agent.id)
						print(f"Deleted agent: {getattr(agent, 'id', '<unknown>')}")
				except Exception as ce:
					print(f"Cleanup warning (delete agent) for {subfolder.name}: {ce}")
				try:
					if vector_store_id:
						retryable(agents_client.vector_stores.delete, vector_store_id)
						print(f"Deleted vector store: {vector_store_id}")
				except Exception as ce2:
					print(f"Cleanup warning (delete vector store) for {subfolder.name}: {ce2}")

	if not all_pairs:
		print("No QA pairs generated. Exiting without writing file.")
		sys.exit(2)

	write_jsonl(all_pairs, OUTPUT_JSONL)


if __name__ == "main" or __name__ == "__main__":  # allow both styles
	main()

