"""
分析模块：用 MiniMax API 从转写文本提炼主推品、话术片段、用户画像、策略
支持 MiniMax API 失败时自动切换 Groq LLM 兜底
"""
import json
import os
import sys
from pathlib import Path

import httpx
from groq import Groq

sys.path.insert(0, str(Path(__file__).parent.parent))

MINIMAX_API_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"

SYSTEM_PROMPT = """你是一位直播电商竞品分析专家。
你的任务是从直播间转写文本中提炼结构化信息，输出严格的 JSON 格式，不要有任何额外说明。"""

ANALYSIS_PROMPT = """以下是直播间「{name}」的转写文本：

{transcript}

请分析并输出以下 JSON 结构（所有字段必填，无内容填空数组或空字符串）：

{{
  "key_products": [
    {{
      "name": "商品名称",
      "price": "价格（如有）",
      "selling_points": ["卖点1", "卖点2"]
    }}
  ],
  "sales_scripts": [
    {{
      "type": "话术类型（开场/逼单/福利/痛点/信任背书等）",
      "content": "原文话术片段",
      "timestamp": "时间戳（如有）"
    }}
  ],
  "user_persona": "目标用户画像描述（年龄、需求、痛点等）",
  "strategy_summary": "整体话术策略总结（2-3句话）",
  "highlights": [
    "值得关注的亮点或异常点1",
    "值得关注的亮点或异常点2"
  ]
}}"""


def analyze_transcript(transcript_text: str, competitor_name: str) -> dict:
    """
    调用 MiniMax API 分析转写文本，返回结构化结果。
    """
    api_key = os.getenv("MINIMAX_API_KEY", "")
    group_id = os.getenv("MINIMAX_GROUP_ID", "")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY 未设置，请在 .env 或环境变量中配置")
    if not group_id:
        raise ValueError("MINIMAX_GROUP_ID 未设置，请在 .env 或环境变量中配置")

    print(f"[analyzer] 开始分析: {competitor_name}，使用模型: MiniMax-M2.5")

    prompt = ANALYSIS_PROMPT.format(
        name=competitor_name,
        transcript=transcript_text[:12000],
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    # ---- 主体：尝试 MiniMax ----
    minimax_exc: Exception | None = None
    try:
        resp = httpx.post(
            f"{MINIMAX_API_URL}?GroupId={group_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "MiniMax-M2.5",
                "messages": messages,
                "temperature": 0.1,
            },
            timeout=60,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        choices = resp_data.get("choices")
        if not choices:
            print(f"[analyzer] 完整响应: {json.dumps(resp_data, ensure_ascii=False)[:500]}")
            raise ValueError(f"MiniMax API 返回无效响应: choices={choices}")
        raw = choices[0]["message"]["content"].strip()
        provider = "MiniMax"
    except httpx.HTTPStatusError as e:
        minimax_exc = e
        print(f"[analyzer] MiniMax HTTP 错误 {e.response.status_code}，尝试 Groq 兜底...")
    except Exception as e:
        minimax_exc = e
        print(f"[analyzer] MiniMax 调用异常 '{type(e).__name__}，尝试 Groq 兜底...")

    # ---- 兜底：Groq LLM ----
    if minimax_exc is not None:
        groq_api_key = os.getenv("GROQ_API_KEY", "")
        if not groq_api_key:
            raise minimax_exc  # 没有 Groq key，原地抛 MiniMax 异常

        print(f"[analyzer] 使用 Groq llama-3.3-70b-versatile 进行分析...")
        groq_client = Groq(api_key=groq_api_key)
        groq_resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.1,
        )
        raw = groq_resp.choices[0].message.content.strip()
        provider = "Groq"

    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    print(f"[analyzer] 分析完成（{provider}），主推品: {len(result.get('key_products', []))} 个")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python claude_analyze.py <txt文件路径> [账号名]")
        sys.exit(1)
    txt_path = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else txt_path.stem
    text = txt_path.read_text(encoding="utf-8")
    result = analyze_transcript(text, name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
