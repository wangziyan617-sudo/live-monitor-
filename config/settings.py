import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# 录屏配置
RECORD_DURATION_SECONDS = 900  # 每个直播间录制15分钟
RECORD_OUTPUT_DIR = BASE_DIR / "storage" / "videos"
ARCHIVE_DIR = BASE_DIR / "storage" / "archive"
DB_PATH = BASE_DIR / "storage" / "data.db"

# Whisper配置
WHISPER_MODEL = "large-v3"  # M系列芯片可以跑
WHISPER_DEVICE = "auto"     # 自动选择 mps/cpu

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# 定时任务
SCHEDULE_HOUR = 10
SCHEDULE_MINUTE = 0

# 竞品账号列表，格式: {"name": "显示名", "url": "直播间主页URL或直播间URL"}
# 先用作业帮测试，后续补充
COMPETITORS = [
    {
        "name": "作业帮课程",
        "douyin_id": "zuoyebang_ke",
        "url": "https://www.douyin.com/user/zuoyebang_ke",
        "live_url": None,  # 暂无直播间ID，用主页检测
    },
    {
        "name": "猿辅导",
        "douyin_id": "90127779527",
        "url": "https://www.douyin.com/user/MS4wLjABAAAAePqU79VZAxZ3bImup0-yP4W7Y7ys3N8CY4JUaifU-TT3dSFuPjTwU7kp965bp6BY",
        "live_url": "https://live.douyin.com/90127779527",  # 直播间直链
    },
]
