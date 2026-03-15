"""
文件记录数据模型
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from datetime import datetime


class FileProcessingStatus(Enum):
    """文件处理状态"""
    PENDING = 'pending'
    PROCESSING = 'processing'
    SUCCESS = 'success'
    FAILED = 'failed'
    SKIPPED_DUPLICATE = 'skipped_duplicate'


@dataclass
class FileRecord:
    """文件记录"""
    source_path: str
    target_path: str
    status: str
    error_message: Optional[str] = None
    file_hash: Optional[str] = None
    processed_at: Optional[str] = None
    file_size: int = 0


@dataclass
class ProcessingStats:
    """处理统计信息"""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped_duplicate: int = 0
    processing: int = 0
    live_photo_pairs: int = 0
    video_files: int = 0

    def to_dict(self) -> dict:
        return {
            'total': self.total,
            'success': self.success,
            'failed': self.failed,
            'skipped_duplicate': self.skipped_duplicate,
            'processing': self.processing,
            'live_photo_pairs': self.live_photo_pairs,
            'video_files': self.video_files
        }
