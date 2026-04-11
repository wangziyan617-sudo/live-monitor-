"""
分析模块：用 MiniMax API 从转写文本提炼主推品、话术片段、用户画像、策略
"""
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

MINIMAX_API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"

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
    if not api_key:
        raise ValueError("MINIMAX_API_KEY 未设置")

    print(f"[analyzer] 开始分析: {competitor_name}")

    prompt = ANALYSIS_PROMPT.format(
        name=competitor_name,
        transcript=transcript_text[:12000],
    )

    resp = httpx.post(
        MINIMAX_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "MiniMax-Text-01",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        },
        timeout=60,
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"].strip()

    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    print(f"[analyzer] 分析完成，主推品: {len(result.get('key_products', []))} 个")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python minimax_analyze.py <txt文件路径> [账号名]")
        sys.exit(1)
    txt_path = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else txt_path.stem
    text = txt_path.read_text(encoding="utf-8")
    result = analyze_transcript(text, name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
