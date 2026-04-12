"""
SQLite 存储模块
表结构：
- competitors: 竞品账号
- sessions: 每次录制会话
- transcripts: 转写文本（关联 session）
- analysis: Claude 分析结果（关联 session）
"""
import subprocess
import sys
from pathlib import Path
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Float, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Competitor(Base):
    __tablename__ = "competitors"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    douyin_id = Column(String)
    url = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    sessions = relationship("Session", back_populates="competitor")


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"))
    recorded_at = Column(DateTime, default=datetime.now)
    video_path = Column(String)
    duration = Column(Float)
    status = Column(String, default="recorded")  # recorded / transcribed / analyzed
    competitor = relationship("Competitor", back_populates="sessions")
    transcript = relationship("Transcript", back_populates="session", uselist=False)
    analysis = relationship("Analysis", back_populates="session", uselist=False)


class Transcript(Base):
    __tablename__ = "transcripts"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), unique=True)
    archive_path = Column(String)   # txt 文件路径
    full_text = Column(Text)        # 完整转写内容
    created_at = Column(DateTime, default=datetime.now)
    session = relationship("Session", back_populates="transcript")


class Analysis(Base):
    __tablename__ = "analysis"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), unique=True)
    key_products = Column(Text)     # JSON: 主推品列表
    sales_scripts = Column(Text)    # JSON: 话术片段
    user_persona = Column(Text)     # 用户画像描述
    strategy_summary = Column(Text) # 话术策略总结
    highlights = Column(Text)       # 重点 highlight
    created_at = Column(DateTime, default=datetime.now)
    session = relationship("Session", back_populates="analysis")


def init_db():
    Base.metadata.create_all(engine)


def get_video_duration(video_path: str) -> float:
    """
    用 ffprobe 读取视频文件的实际时长（秒）。失败时返回 0，不中断流程。
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[db] ffprobe 读取时长失败: {e}")
    return 0.0


def get_or_create_competitor(name: str, douyin_id: str = "", url: str = "") -> Competitor:
    db = SessionLocal()
    try:
        c = db.query(Competitor).filter_by(name=name).first()
        if not c:
            c = Competitor(name=name, douyin_id=douyin_id, url=url)
            db.add(c)
            db.commit()
            db.refresh(c)
        return c
    finally:
        db.close()


def create_session(competitor_name: str, video_path: str, duration: float = 0) -> int:
    db = SessionLocal()
    try:
        c = db.query(Competitor).filter_by(name=competitor_name).first()
        s = Session(competitor_id=c.id, video_path=video_path, duration=duration)
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def save_transcript(session_id: int, archive_path: str, full_text: str):
    db = SessionLocal()
    try:
        t = Transcript(session_id=session_id, archive_path=archive_path, full_text=full_text)
        db.add(t)
        s = db.query(Session).get(session_id)
        s.status = "transcribed"
        db.commit()
    finally:
        db.close()


def save_analysis(session_id: int, result: dict):
    import json
    db = SessionLocal()
    try:
        a = Analysis(
            session_id=session_id,
            key_products=json.dumps(result.get("key_products", []), ensure_ascii=False),
            sales_scripts=json.dumps(result.get("sales_scripts", []), ensure_ascii=False),
            user_persona=result.get("user_persona", ""),
            strategy_summary=result.get("strategy_summary", ""),
            highlights=json.dumps(result.get("highlights", []), ensure_ascii=False),
        )
        db.add(a)
        s = db.query(Session).get(session_id)
        s.status = "analyzed"
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print(f"数据库初始化完成: {DB_PATH}")
