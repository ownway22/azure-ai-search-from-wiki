"""
互動式示範：使用 .env 中的 Azure AI Foundry 設定連線到專案，
建立使用 MODEL_DEPLOYMENT_NAME 的 Agent，並以 thread/message/run 迴圈互動。

必備 .env 變數：
- PROJECT_ENDPOINT: Azure AI Project endpoint（例如：https://<resource>.services.ai.azure.com/api/projects/<project-name>）
- MODEL_DEPLOYMENT_NAME: 你在專案中部署的模型名稱（若要使用 o3，請將此值設為對應的 o3 部署名稱）
"""

from __future__ import annotations

import os
import time
from typing import Optional

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import ListSortOrder


def _extract_text(content) -> str:
    """盡可能將訊息內容轉為純文字。"""
    if isinstance(content, str):
        return content
    try:
        parts = []
        for part in content or []:
            text_val: Optional[str] = None
            if hasattr(part, "text") and getattr(part, "text"):
                text_obj = getattr(part, "text")
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


def main() -> None:
    load_dotenv(override=True)

    endpoint = os.environ.get("PROJECT_ENDPOINT")
    model_deployment = "o3"

    if not endpoint:
        raise SystemExit("Error: PROJECT_ENDPOINT is not set in .env")
    if not model_deployment:
        raise SystemExit("Error: MODEL_DEPLOYMENT_NAME is not set in .env")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    project_client = AIProjectClient(credential=credential, endpoint=endpoint)

    with project_client:
        agents_client = project_client.agents

        # 建立一次性的 Agent 與 Thread 以便互動（離開時會刪除）
        agent = agents_client.create_agent(
            model=model_deployment,
            name="o3_reasoning_demo",
            description="",
            instructions=(
                "你是一位有條理的助理。請清楚表達推理步驟（若模型支援）與最後答案。"
            ),
        )
        thread = agents_client.threads.create()

        print("Session is ready. Type 'exit' to end.")
        try:
            while True:
                user_input = input("You: ")
                if user_input.strip().lower() == "exit":
                    break

                # 新增使用者訊息
                agents_client.messages.create(thread_id=thread.id, role="user", content=user_input)

                # 建立 run 並輪詢完成狀態
                run = agents_client.runs.create(thread_id=thread.id, agent_id=agent.id)
                for _ in range(60):
                    run = agents_client.runs.get(thread_id=thread.id, run_id=run.id)
                    if run.status in ("completed", "failed", "cancelled", "expired"):
                        break
                    time.sleep(2)

                # 取得訊息並印出最後的助理回覆
                msgs = agents_client.messages.list(thread_id=thread.id, order=ListSortOrder.ASCENDING)
                final_answer = ""
                for m in msgs:
                    if getattr(m, "role", None) == "assistant":
                        final_answer = _extract_text(getattr(m, "content", None)) or final_answer

                print(f"\n🤖 AI Agent(o3 model): {final_answer}\n")

        except KeyboardInterrupt:
            print("\nSession terminated by user.")
        finally:
            # 清理資源（可選）
            try:
                agents_client.threads.delete(thread.id)
            except Exception:
                pass
            try:
                agents_client.delete_agent(agent.id)
            except Exception:
                pass
            print("Session closed.")


if __name__ == "__main__":
    main()
