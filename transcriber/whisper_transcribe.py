"""
转写模块：用 Groq Whisper API 将录制的视频转为文字，归档到 storage/archive/
- 免费，云端 API，不需要本地模型
- 输出带时间戳的完整转写文本
- Groq 免费 tier 有严格限流：429 时自动指数退避重试，最多重试 3 次
"""
import os
import sys
import time
from pathlib import Path
from datetime import datetime

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import ARCHIVE_DIR


def transcribe_video(video_path: Path, competitor_name: str) -> Path:
    """
    调用 Groq Whisper API 转写视频，返回归档的 txt 文件路径。
    """
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY 未设置，请在 .env 或环境变量中配置")

    client = Groq(api_key=api_key)

    print(f"[transcriber] 调用 Groq Whisper 转写: {video_path.name}")
    with open(video_path, "rb") as f:
        audio_file = f
        # Groq 免费 tier 严格限流：429 时指数退避重试，最多 3 次
        last_exc: Exception | None = None
        for attempt in range(4):  # 0=首次，1-3=重试
            if attempt > 0:
                wait_secs = 2 ** attempt
                print(f"[transcriber] Rate limited, retrying in {wait_secs}s... (attempt {attempt}/3)")
                time.sleep(wait_secs)
                # 文件指针已耗尽，需重新打开
                audio_file = open(video_path, "rb")
            try:
                result = client.audio.transcriptions.create(
                    file=(video_path.name, audio_file),
                    model="whisper-large-v3",
                    language="zh",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
                # 成功：若是非首次且重新打开了文件则关闭它
                if attempt > 0:
                    audio_file.close()
                break
            except httpx.HTTPStatusError as e:
                last_exc = e
                if e.response.status_code == 429:
                    if attempt < 3:
                        continue  # 进入下一次循环（sleep + reopen + retry）
                    # 重试耗尽
                    if attempt > 0:
                        audio_file.close()
                    raise
                else:
                    if attempt > 0:
                        audio_file.close()
                    raise
        else:
            # 循环正常结束（all attempts exhausted）但没有 break
            if attempt > 0:
                try:
                    audio_file.close()
                except Exception:
                    pass
            raise RuntimeError(f"Groq Whisper 全部重试失败，最后异常: {last_exc}") from last_exc

    segments = result.segments or []
    # segments 可能是 dict 列表（verbose_json 格式）或对象列表，兼容两种
    def _get(seg, key):
        return seg[key] if isinstance(seg, dict) else getattr(seg, key)

    duration = _get(segments[-1], 'end') if segments else 0
    print(f"[transcriber] 转写完成，时长约 {duration:.1f}s，共 {len(segments)} 段")

    lines = []
    for seg in segments:
        start = _get(seg, 'start')
        end = _get(seg, 'end')
        text = _get(seg, 'text')
        timestamp = f"[{_fmt_time(start)} --> {_fmt_time(end)}]"
        lines.append(f"{timestamp} {text.strip()}")

    # 归档路径: archive/猿辅导/20260412/20260412_1000.txt
    date_str = datetime.now().strftime("%Y%m%d")
    time_str = datetime.now().strftime("%Y%m%d_%H%M")
    safe_name = competitor_name.replace(" ", "_")
    out_dir = ARCHIVE_DIR / safe_name / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{time_str}.txt"

    header = (
        f"账号: {competitor_name}\n"
        f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"视频: {video_path.name}\n"
        f"时长: {duration:.1f}s\n"
        f"{'='*60}\n"
        "\n"
    )
    out_path.write_text(header + "\n".join(lines), encoding="utf-8")
    print(f"[transcriber] 转写归档: {out_path}")
    return out_path


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python whisper_transcribe.py <视频路径> [账号名]")
        sys.exit(1)
    vp = Path(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else vp.stem
    result = transcribe_video(vp, name)
    print(f"完成: {result}")
