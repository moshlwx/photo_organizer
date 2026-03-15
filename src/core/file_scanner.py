"""
文件扫描器 - 扫描源目录中的所有媒体文件
"""
import os
import logging
from pathlib import Path
from typing import List

from config.settings import SUPPORTED_MEDIA_FORMATS


class FileScanner:
    """文件扫描器"""

    def __init__(self, source_dir: Path, stop_requested_func):
        """
        初始化扫描器

        Args:
            source_dir: 源目录
            stop_requested_func: 检查是否请求停止的函数
        """
        self.source_dir = source_dir
        self.stop_requested_func = stop_requested_func
        self.logger = logging.getLogger('FileScanner')

    def scan_media_files(self) -> List[Path]:
        """
        扫描所有照片和视频文件

        Returns:
            媒体文件路径列表
        """
        media_files = []
        scanned_count = 0

        self.logger.info("开始扫描媒体文件（照片和视频）...")

        try:
            for root, dirs, files in os.walk(self.source_dir):
                if self.stop_requested_func():
                    break

                for filename in files:
                    if self.stop_requested_func():
                        break

                    # 检查文件扩展名
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in SUPPORTED_MEDIA_FORMATS:
                        file_path = Path(root) / filename
                        media_files.append(file_path)

                    scanned_count += 1
                    if scanned_count % 1000 == 0:
                        media_count = len(media_files)
                        self.logger.info(f"已扫描 {scanned_count} 个文件，找到 {media_count} 个媒体文件")

            self.logger.info(f"扫描完成，共扫描 {scanned_count} 个文件，找到 {len(media_files)} 个媒体文件")
            return media_files

        except Exception as e:
            self.logger.error(f"扫描文件失败: {e}")
            raise
