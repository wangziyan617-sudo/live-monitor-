"""
转写模块：用 Groq Whisper API 将录制的视频转为文字，归档到 storage/archive/
- 免费，云端 API，不需要本地模型
- 输出带时间戳的完整转写文本
"""
import os
import sys
from pathlib import Path
from datetime import datetime

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
        result = client.audio.transcriptions.create(
            file=(video_path.name, f),
            model="whisper-large-v3",
            language="zh",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = result.segments or []
    duration = segments[-1].end if segments else 0
    print(f"[transcriber] 转写完成，时长约 {duration:.1f}s，共 {len(segments)} 段")

    lines = []
    for seg in segments:
        timestamp = f"[{_fmt_time(seg.start)} --> {_fmt_time(seg.end)}]"
        lines.append(f"{timestamp} {seg.text.strip()}")

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
        f"{'='*60}\n\n"
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
