from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
import datetime

from database import Base

class TranscriptionQueueItem(Base):
    __tablename__ = "transcription_queue"

    id = Column(Integer, primary_key=True, index=True)
    book_pair_id = Column(Integer, ForeignKey("book_pairs.id"), unique=True, index=True, nullable=False)
    
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
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationship to BookPair 
    # (assuming BookPair model exists and back_populates optionally)
    book_pair = relationship("BookPair", backref="transcription_queue_item")
