"""
主调度器：每日定时执行完整流程
1. 录制各竞品直播间
2. Whisper 转写
3. Claude 分析
4. 存入数据库
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

sys.path.insert(0, str(Path(__file__).parent))
from config.settings import COMPETITORS, SCHEDULE_HOUR, SCHEDULE_MINUTE, RECORD_DURATION_SECONDS
from crawler.recorder import record_live_room
from transcriber.whisper_transcribe import transcribe_video
from analyzer.claude_analyze import analyze_transcript
from storage.db import (
    init_db, get_or_create_competitor, create_session,
    save_transcript, save_analysis
)


async def process_competitor(competitor: dict):
    """完整处理单个竞品：录制 → 转写 → 分析 → 存库"""
    name = competitor["name"]
    print(f"\n{'='*50}")
    print(f"[scheduler] 开始处理: {name}  {datetime.now().strftime('%H:%M:%S')}")

    # 确保竞品记录存在
    get_or_create_competitor(
        name=name,
        douyin_id=competitor.get("douyin_id", ""),
        url=competitor.get("url", ""),
    )

    # Step 1: 录制
    video_path = await record_live_room(competitor, duration=RECORD_DURATION_SECONDS)
    if not video_path:
        print(f"[scheduler] {name} 未在直播，跳过")
        return

    session_id = create_session(name, str(video_path))

    # Step 2: 转写
    try:
        archive_path = transcribe_video(video_path, name)
        full_text = archive_path.read_text(encoding="utf-8")
        save_transcript(session_id, str(archive_path), full_text)
    except Exception as e:
        print(f"[scheduler] 转写失败: {e}")
        return

    # Step 3: 分析
    try:
        result = analyze_transcript(full_text, name)
        save_analysis(session_id, result)
    except Exception as e:
        print(f"[scheduler] 分析失败: {e}")
        return

    print(f"[scheduler] {name} 处理完成")


async def run_daily_job():
    """每日任务：串行处理所有竞品（避免同时开多个浏览器）"""
    print(f"\n[scheduler] 每日任务开始 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    for competitor in COMPETITORS:
        try:
            await process_competitor(competitor)
        except Exception as e:
            print(f"[scheduler] 处理 {competitor['name']} 时出错: {e}")
    print(f"[scheduler] 每日任务完成 {datetime.now().strftime('%H:%M:%S')}")


def job():
    asyncio.run(run_daily_job())


if __name__ == "__main__":
    init_db()

    import sys
    if "--now" in sys.argv:
        # 立即执行一次（测试用）
        print("[scheduler] 立即执行模式")
        job()
    else:
        # 定时模式
        scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(job, "cron", hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE)
        print(f"[scheduler] 定时任务已启动，每天 {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} 执行")
        print("[scheduler] Ctrl+C 退出")
        try:
            scheduler.start()
        except KeyboardInterrupt:
            print("[scheduler] 已停止")
