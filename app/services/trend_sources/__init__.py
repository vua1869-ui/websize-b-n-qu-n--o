"""Package chứa các Adapter nguồn dữ liệu xu hướng (Google Trends, Demo, v.v.)."""
from app.services.trend_sources.base import BaseTrendSource
from app.services.trend_sources.demo_source import DemoTrendSource
from app.services.trend_sources.google_trends import GoogleTrendsSource

__all__ = ["BaseTrendSource", "GoogleTrendsSource", "DemoTrendSource"]
