import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# 录屏配置
RECORD_DURATION_SECONDS = 900  # 每个直播间录制15分钟
RECORD_OUTPUT_DIR = BASE_DIR / "storage" / "videos2"
ARCHIVE_DIR = BASE_DIR / "storage" / "archive2"
DB_PATH = BASE_DIR / "storage" / "data2.db"

# 定时任务
SCHEDULE_HOUR = 20
SCHEDULE_MINUTE = 0

# 竞品直播间列表 —— 语文/数学/英语竞品（第二看板）
COMPETITORS = [
    {"name": "窦昕文史阅写拔尖",         "live_url": "https://live.douyin.com/509053463862"},
    {"name": "豆神读写文史拔尖",           "live_url": "https://live.douyin.com/78850894708"},
    {"name": "豆神教育旗舰店",             "live_url": "https://live.douyin.com/77601910410"},
    {"name": "叫叫阅读-21天阅读进阶",     "live_url": "https://live.douyin.com/68249410644"},
    {"name": "叫叫-21天阅读习惯养成",     "live_url": "https://live.douyin.com/93663014486"},
    {"name": "小鹿大阅读S版-千帆",         "live_url": "https://live.douyin.com/90058235253"},
    {"name": "小鹿大阅读",                 "live_url": "https://live.douyin.com/28978225013"},
    {"name": "猿辅导当堂满分大语文",       "live_url": "https://live.douyin.com/51152631046"},
    {"name": "跟清北名师学全年数学",       "live_url": "https://live.douyin.com/35647478396"},
    {"name": "清北老师数学名师堂",         "live_url": "https://live.douyin.com/jyd4976154816"},
    {"name": "清华猴神侯子疼",             "live_url": "https://live.douyin.com/ZhuangyuanHouge"},
    {"name": "张文晖-满分数学张老师",     "live_url": "https://live.douyin.com/77889012h"},
    {"name": "猿辅导奥数官号",             "live_url": "https://live.douyin.com/52168303844"},
    {"name": "小鹿素养-小学英语官旗店",   "live_url": "https://live.douyin.com/Laokkaopu"},
    {"name": "小鹿素养小学英语大通关",     "live_url": "https://live.douyin.com/83176828970"},
    {"name": "猿辅导官方-剑桥双优",       "live_url": "https://live.douyin.com/87392629160"},
]
