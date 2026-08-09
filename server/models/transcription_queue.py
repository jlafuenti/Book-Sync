from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
import datetime

from database import Base

class TranscriptionQueueItem(Base):
    __tablename__ = "transcription_queue"

    id = Column(Integer, primary_key=True, index=True)
    book_pair_id = Column(Integer, ForeignKey("book_pairs.id"), index=True, nullable=False)
    
    # "pending", "in_progress", "completed", "failed", "cancelled"
    status = Column(String, default="pending", nullable=False)
    
    # Lower number = higher priority. Default 100
    priority = Column(Integer, default=100, nullable=False)
    position = Column(Integer, default=0, nullable=False)
    
    # 0.0 to 1.0 progress
    progress = Column(Float, default=0.0, nullable=False)
    
    # Human readable message
    message = Column(String, default="Waiting in queue")
    
    # Error details if failed
    error_message = Column(Text, nullable=True)
    
    # Number of times this item has been retried due to provider unavailability
    retry_count = Column(Integer, default=0, nullable=False)

    # "Run now" override (issue #106): dispatch this item even when the
    # off-hours window is closed. Sticky for the life of the item, so a job
    # forced at 14:00 also isn't paused when the window would have closed.
    force_run = Column(Boolean, default=False, server_default="false", nullable=False)

    # Set when a running job was paused at a chunk boundary because the
    # off-hours window closed. Doubles as the marker for "a resumable
    # checkpoint is waiting on the transcription worker": paused items are
    # re-dispatched ahead of fresh ones so a half-done book finishes first.
    paused_at = Column(DateTime, nullable=True)


    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationship to BookPair 
    # (assuming BookPair model exists and back_populates optionally)
    book_pair = relationship("BookPair", backref="transcription_queue_item")
