import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# 录屏配置
RECORD_DURATION_SECONDS = 1200  # 每个直播间录制20分钟
RECORD_OUTPUT_DIR = BASE_DIR / "storage" / "videos"
ARCHIVE_DIR = BASE_DIR / "storage" / "archive"
DB_PATH = BASE_DIR / "storage" / "data.db"

# 定时任务
SCHEDULE_HOUR = 10
SCHEDULE_MINUTE = 0

# 竞品直播间列表，格式: {"name": "显示名", "live_url": "live.douyin.com/数字"}
COMPETITORS = [
    {
        "name": "猿辅导新思维官店",
        "live_url": "https://live.douyin.com/590298532255",
    },
    {
        "name": "猿辅导思维",
        "live_url": "https://live.douyin.com/218786291237",
    },
    {
        "name": "猿辅导",
        "live_url": "https://live.douyin.com/90127779527",
    },
]
