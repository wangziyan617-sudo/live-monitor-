"""
主调度器：每日定时执行完整流程
1. 并行录制所有竞品（同时录，最大化效率）
2. 并行转写 + 分析已录制的视频
3. 存入数据库
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pathlib import Path
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

sys.path.insert(0, str(Path(__file__).parent))

# --group 参数决定使用哪套配置（默认 group1 = config.settings）
_group = "group1"
if "--group" in sys.argv:
    _idx = sys.argv.index("--group")
    if _idx + 1 < len(sys.argv):
        _group = sys.argv[_idx + 1]

if _group == "group2":
    from config.settings2 import COMPETITORS, SCHEDULE_HOUR, SCHEDULE_MINUTE, RECORD_DURATION_SECONDS
    from config.settings2 import DB_PATH as _DB_PATH, RECORD_OUTPUT_DIR as _RECORD_OUTPUT_DIR, ARCHIVE_DIR as _ARCHIVE_DIR
else:
    from config.settings import COMPETITORS, SCHEDULE_HOUR, SCHEDULE_MINUTE, RECORD_DURATION_SECONDS
    from config.settings import DB_PATH as _DB_PATH, RECORD_OUTPUT_DIR as _RECORD_OUTPUT_DIR, ARCHIVE_DIR as _ARCHIVE_DIR

# 在 import storage.db 之前切换数据库路径，确保所有函数拿到正确的 SessionLocal
import storage.db as _db_module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
_db_module.DB_PATH = str(_DB_PATH)
_db_module.DATABASE_URL = f"sqlite:///{_DB_PATH}"
_db_module.engine = create_engine(_db_module.DATABASE_URL, connect_args={"check_same_thread": False})
_db_module.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db_module.engine)

from crawler.hls_recorder import fetch_m3u8_url, record_m3u8
from crawler.recorder import record_live_room
from transcriber.whisper_transcribe import transcribe_video
from analyzer.claude_analyze import analyze_transcript
from storage.db import (
    init_db, get_or_create_competitor, create_session,
    save_transcript, save_analysis, get_video_duration,
    Analysis, Competitor, Session, Transcript, SessionLocal
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
def transcribe_one(session_id: int, competitor_name: str, video_path: Path | None, skip_transcribe: bool = False):
    db = SessionLocal()

    # 分析（已转写的会话只需分析，已录制的需要先转写）
    try:
        if skip_transcribe:
            # 已转写，直接取 transcript
            t = db.query(Transcript).filter_by(session_id=session_id).first()
            if not t or not t.full_text:
                print(f"[scheduler] session={session_id} 无转写文本，跳过分析")
                db.close()
                return
            full_text = t.full_text
            print(f"[scheduler] session={session_id} 复用已有转写（{len(full_text)}字）")
        else:
            if not video_path or not video_path.exists():
                print(f"[scheduler] session={session_id} 视频不存在，跳过")
                db.close()
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
                db.close()
                return
            # 清理视频
            try:
                if video_path.exists():
                    video_path.unlink()
                    print(f"[scheduler] 清理视频: {video_path.name}")
            except Exception as e:
                print(f"[scheduler] 清理视频失败（不影响）: {e}")

        # 分析（所有情况都要跑）
        result = analyze_transcript(full_text, competitor_name)
        save_analysis(session_id, result)
        s = db.query(Session).filter_by(id=session_id).first()
        if s: s.status = "analyzed"
        db.commit()
        print(f"[scheduler] session={session_id} 分析完成")
    except Exception as e:
        print(f"[scheduler] session={session_id} 分析失败: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


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
        print(f"[scheduler] Phase 2: 分批转写+分析 {len(pending)} 场（每批2个，防止Groq限流）…")
        import queue
        pending_queue = queue.Queue()
        for item in pending:
            pending_queue.put(item)
        def worker():
            while True:
                try:
                    sid, name, vp = pending_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    transcribe_one(sid, name, vp)
                except Exception as e:
                    print(f"[scheduler] session={sid}({name}) 异常: {e}")
                finally:
                    pending_queue.task_done()
        workers = []
        for _ in range(min(2, len(pending))):
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            workers.append(t)
        for t in workers:
            t.join()
    else:
        print("[scheduler] 所有场次已有分析，跳过 Phase 2")

    print(f"[scheduler] === 每日任务完成 {datetime.now().strftime('%H:%M:%S')} ===")


if __name__ == "__main__":
    init_db()

    # --phase2-only: 跳过录制，只跑 Phase2（转写+分析已有视频）
    if "--phase2-only" in sys.argv or "--reanalyze" in sys.argv:
        db = SessionLocal()
        # transcribed 状态的会话：已有转写文本，只需分析
        transcribed = db.query(Session).filter_by(status="transcribed").all()
        # recorded 状态的会话：已有视频，需要转写+分析
        recorded = db.query(Session).filter_by(status="recorded").all()

        tasks_transcribe = []   # (session_id, name, video_path)
        tasks_analyze = []      # (session_id, name)

        for s in transcribed:
            tasks_analyze.append((s.id, s.competitor.name))
        for s in recorded:
            has_transcript = db.query(Transcript).filter_by(session_id=s.id).first()
            if not has_transcript:
                vp = Path(s.video_path) if s.video_path else None
                tasks_transcribe.append((s.id, s.competitor.name, vp))

        if tasks_analyze:
            print(f"[scheduler] Phase2: 分析 {len(tasks_analyze)} 个已转写会话…")
            with ThreadPoolExecutor(max_workers=len(tasks_analyze)) as pool:
                futures = {pool.submit(transcribe_one, sid, name, None, True): (sid, name)
                           for sid, name in tasks_analyze}
                for future in as_completed(futures):
                    sid, name = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[scheduler] session={sid}({name}) 异常: {e}")

        if tasks_transcribe:
            print(f"[scheduler] Phase2: 转写+分析 {len(tasks_transcribe)} 个已录制会话…")
            with ThreadPoolExecutor(max_workers=len(tasks_transcribe)) as pool:
                futures = {pool.submit(transcribe_one, sid, name, vp, False): (sid, name)
                           for sid, name, vp in tasks_transcribe}
                for future in as_completed(futures):
                    sid, name = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[scheduler] session={sid}({name}) 异常: {e}")

        if not tasks_analyze and not tasks_transcribe:
            print("[scheduler] 没有需要处理的任务，跳过 Phase2")
        db.close()
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
