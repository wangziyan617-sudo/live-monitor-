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

# 定时任务
SCHEDULE_HOUR = 10
SCHEDULE_MINUTE = 0

# 竞品直播间列表，格式: {"name": "显示名", "live_url": "live.douyin.com/数字"}
COMPETITORS = [
    {
        "name": "一本官方旗舰店小学",
        "live_url": "https://live.douyin.com/765818680646",
    },
    {
        "name": "一本小学好书推荐",
        "live_url": "https://live.douyin.com/651085849971",
    },
    {
        "name": "一本官方旗舰店",
        "live_url": "https://live.douyin.com/916730480866",
    },
    {
        "name": "一本小学好书严选",
        "live_url": "https://live.douyin.com/478081890806",
    },
    {
        "name": "一本小学好物推荐",
        "live_url": "https://live.douyin.com/23374541982",
    },
    {
        "name": "一本图书官方旗舰店小学",
        "live_url": "https://live.douyin.com/229143416416",
    },
    # 以下账号目前未直播（douyin号：yanlaoshi1989/95659250970/73963897007/20349514918）
]
