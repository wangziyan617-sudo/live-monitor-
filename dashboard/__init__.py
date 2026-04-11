"""
dashboard 包入口，挂载静态文件
"""
from pathlib import Path
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"
