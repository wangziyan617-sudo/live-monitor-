"""
主调度器：每日定时执行完整流程
1. 并行录制所有竞品（同时录，最大化效率）
2. 并行转写 + 分析已录制的视频
3. 存入数据库
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import COMPETITORS, SCHEDULE_HOUR, SCHEDULE_MINUTE, RECORD_DURATION_SECONDS
from crawler.hls_recorder import fetch_m3u8_url, record_m3u8
from crawler.recorder import record_live_room
from transcriber.whisper_transcribe import transcribe_video
from analyzer.claude_analyze import analyze_transcript
from storage.db import (
    init_db, get_or_create_competitor, create_session,
    save_transcript, save_analysis, get_video_duration, Analysis, Competitor, SessionLocal
)


def ensure_competitor(name: str, douyin_id: str = "", url: str = "") -> int:
    """确保竞品存在，返回 competitor_id"""
    get_or_create_competitor(name=name, douyin_id=douyin_id, url=url)
    db = SessionLocal()
    try:
        row = db.query(Competitor).filter_by(name=name).first()
        return row.id if row else -1
    finally:
        db.close()


# Phase 1: 单个竞品录制
def record_one(competitor: dict, duration: int):
    import asyncio
    name = competitor["name"]
    print(f"[scheduler] 录制: {name} ({duration}s)")

    competitor_id = ensure_competitor(
        name=name,
        douyin_id=competitor.get("douyin_id", ""),
        url=competitor.get("url", ""),
    )

    video_path = None
    if competitor.get("live_url"):
        try:
            m3u8_url = asyncio.run(fetch_m3u8_url(competitor))
            if m3u8_url:
                video_path = record_m3u8(m3u8_url, competitor, duration=duration)
        except Exception as e:
            print(f"[scheduler] {name} HLS 异常: {e}")

    if not video_path:
        try:
            video_path = asyncio.run(record_live_room(competitor, duration=duration))
        except Exception as e:
            print(f"[scheduler] {name} 屏幕录制异常: {e}")

    if not video_path or not video_path.exists():
        print(f"[scheduler] {name} 录制失败（视频文件不存在）")
        return (name, None, None)

    duration_sec = get_video_duration(str(video_path))
    session_id = create_session(name, str(video_path), duration=duration_sec)
    print(f"[scheduler] {name} 录制完成: {duration_sec:.0f}s -> session_id={session_id}")
    return (name, session_id, video_path)


# Phase 2: 单场转写 + 分析
def transcribe_one(session_id: int, competitor_name: str, video_path: Path):
    if not video_path or not video_path.exists():
        print(f"[scheduler] session={session_id} 视频不存在，跳过")
        return

    # 转写
    try:
        archive_path = transcribe_video(video_path, competitor_name)
        full_text = archive_path.read_text(encoding="utf-8")
        save_transcript(session_id, str(archive_path), full_text)
        print(f"[scheduler] session={session_id} 转写完成（{len(full_text)}字）")
    except Exception as e:
        print(f"[scheduler] session={session_id} 转写失败: {e}")
        import traceback; traceback.print_exc()
        return

    # 分析
    try:
        result = analyze_transcript(full_text, competitor_name)
        save_analysis(session_id, result)
        print(f"[scheduler] session={session_id} 分析完成")
    except Exception as e:
        print(f"[scheduler] session={session_id} 分析失败: {e}")
        import traceback; traceback.print_exc()
        return

    # 清理视频（不再需要）
    try:
        if video_path.exists():
            video_path.unlink()
            print(f"[scheduler] 清理视频: {video_path.name}")
    except Exception as e:
        print(f"[scheduler] 清理视频失败（不影响）: {e}")


# 主调度
def run_daily_job(duration: int):
    print(f"\n[scheduler] === 每日任务开始 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 录制 {duration}s | 竞品数: {len(COMPETITORS)} ===")

    # Phase 1: 并行录制所有竞品
    print(f"[scheduler] Phase 1: 并行录制 {len(COMPETITORS)} 个竞品…")
    with ThreadPoolExecutor(max_workers=len(COMPETITORS)) as pool:
        futures = {pool.submit(record_one, c, duration): c["name"] for c in COMPETITORS}
        results = {}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                print(f"[scheduler] {name} 录制异常: {e}")
                results[name] = (name, None, None)

    successful = {n: v for n, v in results.items() if v[2] is not None}
    failed = [n for n, v in results.items() if v[2] is None]
    print(f"[scheduler] Phase 1 完成: 成功 {len(successful)}/{len(results)} | 失败: {failed}")

    if not successful:
        print("[scheduler] 没有成功录制的场次，退出")
        return

    # Phase 2: 跳过已有分析结果的场次
    db = SessionLocal()
    pending = []
    for name, (_, session_id, video_path) in successful.items():
        existing = db.query(Analysis).filter_by(session_id=session_id).first()
        if existing:
            print(f"[scheduler] session={session_id}({name}) 已有分析，跳过")
        else:
            pending.append((session_id, name, video_path))
    db.close()

    if pending:
        print(f"[scheduler] Phase 2: 并行转写+分析 {len(pending)} 场…")
        with ThreadPoolExecutor(max_workers=len(pending)) as pool2:
            futures2 = {
                pool2.submit(transcribe_one, sid, name, vp): (sid, name)
                for sid, name, vp in pending
            }
            for future in as_completed(futures2):
                sid, name = futures2[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"[scheduler] session={sid}({name}) 异常: {e}")
    else:
        print("[scheduler] 所有场次已有分析，跳过 Phase 2")

    print(f"[scheduler] === 每日任务完成 {datetime.now().strftime('%H:%M:%S')} ===")


if __name__ == "__main__":
    init_db()

    # --phase2-only: 跳过录制，只跑 Phase2（转写+分析已有视频）
    if "--phase2-only" in sys.argv or "--reanalyze" in sys.argv:
        db = SessionLocal()
        # transcribed 状态的会话（Phase2 未完成或 MiniMax 失败的兜底）
        sessions = db.query(Session).filter_by(status="transcribed").all()
        # 全部未分析的会话（含 recorded 但还没转写的）
        pending = [(s.id, s.competitor.name) for s in sessions]
        # 也包含已录制但从未转写的（recorded 状态）
        recorded = db.query(Session).filter_by(status="recorded").all()
        for s in recorded:
            has_transcript = db.query(Transcript).filter_by(session_id=s.id).first()
            if not has_transcript:
                pending.append((s.id, s.competitor.name))
        db.close()

        if not pending:
            print("[scheduler] 没有需要分析的视频，跳过 Phase2")
        else:
            print(f"[scheduler] Phase2: 并行转写+分析 {len(pending)} 个会话…")
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                futures = {pool.submit(transcribe_one, sid, name, None): (sid, name)
                           for sid, name in pending}
                for future in as_completed(futures):
                    sid, name = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[scheduler] session={sid}({name}) 异常: {e}")
        sys.exit(0)

    # --static: 只生成静态文件（不改 DB，不录制）
    if "--static" in sys.argv:
        print("[scheduler] --static: 仅生成静态文件")
        sys.exit(0)

    duration = int(os.environ.get("RECORD_DURATION", RECORD_DURATION_SECONDS))
    if "--duration" in sys.argv:
        idx = sys.argv.index("--duration")
        if idx + 1 < len(sys.argv):
            duration = int(sys.argv[idx + 1])

    if "--now" in sys.argv:
        print(f"[scheduler] 立即执行 | 录制 {duration}s | 竞品数={len(COMPETITORS)}")
        run_daily_job(duration)
    else:
        scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(lambda: run_daily_job(duration), "cron", hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE)
        print(f"[scheduler] 定时任务已启动，每天 {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
        print("[scheduler] Ctrl+C 退出")
        try:
            scheduler.start()
        except KeyboardInterrupt:
            print("[scheduler] 已停止")
