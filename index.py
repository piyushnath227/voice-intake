"""Vercel entrypoint (zero-config FastAPI detects `app` here). Local: uvicorn app.main:app --reload"""
from app.main import app  # noqa: F401
