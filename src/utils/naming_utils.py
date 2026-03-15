"""
文件命名工具
"""
from datetime import datetime
from pathlib import Path
from typing import Dict


def contains_chinese(text: str) -> bool:
    """
    检测文本是否包含中文字符

    Args:
        text: 要检测的文本

    Returns:
        是否包含中文字符
    """
    return any('\u4e00' <= char <= '\u9fff' for char in text)


class FileNamer:
    """文件命名器（支持断点续传）"""

    def __init__(self, renaming_index: Dict[str, int]):
        """
        初始化命名器

        Args:
            renaming_index: 重命名索引字典（YYYYMMDD -> 计数器）
        """
        self.renaming_index = renaming_index or {}

    def get_new_filename(self, file_path: Path, photo_date: datetime,
                       is_video: bool = False) -> str:
        """
        获取新文件名（仅对非中文文件名）

        格式：IMG_YYYYMMDD_XXXX.ext 或 MOV_YYYYMMDD_XXXX.ext

        Args:
            file_path: 原始文件路径
            photo_date: 照片/视频日期
            is_video: 是否为视频文件

        Returns:
            新文件名，如果原文件名包含中文则返回原文件名
        """
        original_name = file_path.stem
        ext = file_path.suffix

        # 如果文件名包含中文，保留原文件名
        if contains_chinese(original_name):
            return file_path.name

        # 确定前缀
        if is_video:
            prefix = 'MOV'
        else:
            prefix = 'IMG'

        # 生成日期键（YYYYMMDD）
        date_key = photo_date.strftime('%Y%m%d')

        # 获取或初始化序号
        if date_key not in self.renaming_index:
            self.renaming_index[date_key] = 1

        # 生成序号（4位数字）
        sequence = self.renaming_index[date_key]
        sequence_str = f"{sequence:04d}"

        # 增加计数器
        self.renaming_index[date_key] += 1

        # 生成新文件名
        new_name = f"{prefix}_{date_key}_{sequence_str}{ext}"

        return new_name

    def get_index(self) -> Dict[str, int]:
        """获取当前的重命名索引"""
        return self.renaming_index
