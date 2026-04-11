"""
FastAPI 后端：提供看板所需的 API 接口
"""
import json
import sys
from pathlib import Path
from datetime import datetime, date

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import desc

sys.path.insert(0, str(Path(__file__).parent.parent))
from storage.db import SessionLocal, Competitor, Session, Transcript, Analysis, init_db

app = FastAPI(title="竞品直播间监控看板")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/competitors")
def get_competitors():
    db = SessionLocal()
    try:
        competitors = db.query(Competitor).all()
        return [{"id": c.id, "name": c.name, "douyin_id": c.douyin_id} for c in competitors]
    finally:
        db.close()


@app.get("/api/sessions")
def get_sessions(
    competitor_id: int = None,
    limit: int = Query(20, le=100),
    date_from: str = None,
):
    db = SessionLocal()
    try:
        q = db.query(Session)
        if competitor_id:
            q = q.filter(Session.competitor_id == competitor_id)
        if date_from:
            dt = datetime.strptime(date_from, "%Y-%m-%d")
            q = q.filter(Session.recorded_at >= dt)
        sessions = q.order_by(desc(Session.recorded_at)).limit(limit).all()
        return [
            {
                "id": s.id,
                "competitor": s.competitor.name,
                "recorded_at": s.recorded_at.isoformat(),
                "status": s.status,
                "duration": s.duration,
            }
            for s in sessions
        ]
    finally:
        db.close()


@app.get("/api/analysis/{session_id}")
def get_analysis(session_id: int):
    db = SessionLocal()
    try:
        a = db.query(Analysis).filter_by(session_id=session_id).first()
        if not a:
            return {"error": "暂无分析结果"}
        return {
            "session_id": session_id,
            "key_products": json.loads(a.key_products or "[]"),
            "sales_scripts": json.loads(a.sales_scripts or "[]"),
            "user_persona": a.user_persona,
            "strategy_summary": a.strategy_summary,
            "highlights": json.loads(a.highlights or "[]"),
            "created_at": a.created_at.isoformat(),
        }
    finally:
        db.close()


@app.get("/api/compare")
def compare_competitors(date_str: str = None):
    """横向对比：同一天所有竞品的分析结果"""
    db = SessionLocal()
    try:
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = date.today()

        sessions = (
            db.query(Session)
            .filter(Session.status == "analyzed")
            .all()
        )
        # 过滤当天
        sessions = [
            s for s in sessions
            if s.recorded_at.date() == target_date
        ]

        result = []
        for s in sessions:
            a = s.analysis
            if not a:
                continue
            result.append({
                "competitor": s.competitor.name,
                "recorded_at": s.recorded_at.isoformat(),
                "key_products": json.loads(a.key_products or "[]"),
                "user_persona": a.user_persona,
                "strategy_summary": a.strategy_summary,
                "highlights": json.loads(a.highlights or "[]"),
            })
        return {"date": str(target_date), "competitors": result}
    finally:
        db.close()


@app.get("/api/transcript/{session_id}")
def get_transcript(session_id: int):
    db = SessionLocal()
    try:
        t = db.query(Transcript).filter_by(session_id=session_id).first()
        if not t:
            return {"error": "暂无转写文本"}
        return {
            "session_id": session_id,
            "archive_path": t.archive_path,
            "full_text": t.full_text,
            "created_at": t.created_at.isoformat(),
        }
    finally:
        db.close()


@app.get("/api/available_dates")
def get_available_dates():
    """返回有数据的日期列表"""
    db = SessionLocal()
    try:
        sessions = db.query(Session).filter(Session.status == "analyzed").all()
        dates = sorted(
            set(s.recorded_at.strftime("%Y-%m-%d") for s in sessions),
            reverse=True,
        )
        return {"dates": dates}
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
