# bot/models.py

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime, timezone

Base = declarative_base()

class Summary(Base):
    __tablename__ = 'summaries'

    id = Column(Integer, primary_key=True, index=True)
    guild_id = Column(String, index=True)
    channel_id = Column(String, index=True)
    user_id = Column(String, index=True)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    summary = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
