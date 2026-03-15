"""
视频元数据读取
"""
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

try:
    from pymediainfo import MediaInfo
    MEDIAINFO_AVAILABLE = True
except ImportError:
    MEDIAINFO_AVAILABLE = False


def get_video_creation_date(file_path: Path) -> Optional[datetime]:
    """
    获取视频的拍摄时间（通过 MediaInfo 读取元数据）

    Args:
        file_path: 视频文件路径

    Returns:
        视频的拍摄时间
    """
    logger = logging.getLogger('MediaInfo')

    if not MEDIAINFO_AVAILABLE:
        logger.warning(f"pymediainfo 未安装，无法读取视频元数据，使用文件修改时间: {file_path.name}")
        try:
            return datetime.fromtimestamp(file_path.stat().st_mtime)
        except Exception:
            return None

    try:
        media_info = MediaInfo.parse(str(file_path))

        # 优先尝试获取 Track 的 Tagged_date 或 Encoded_date
        for track in media_info.tracks:
            if track.track_type == 'Video':
                # 尝试多种可能的日期字段
                for date_field in ['tagged_date', 'encoded_date', 'creation_date']:
                    if hasattr(track, date_field):
                        date_str = getattr(track, date_field)
                        if date_str:
                            # MediaInfoInfo 返回的日期格式通常是: "UTC 2024-01-01 12:00:00"
                            # 或 "2024-01-01 12:00:00"
                            try:
                                # 尝试去除 "UTC " 前缀
                                if date_str.startswith('UTC '):
                                    date_str = date_str[4:]

                                # 尝试解析不同格式
                                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                                    try:
                                        return datetime.strptime(date_str, fmt)
                                    except ValueError:
                                        continue
                            except Exception as e:
                                logger.debug(f"解析视频日期失败 {date_field}={date_str}: {e}")
                                continue

        # 如果没有找到视频轨道的日期，尝试获取通用轨道的日期
        for track in media_info.tracks:
            if track.track_type == 'General':
                for date_field in ['tagged_date', 'encoded_date', 'file_creation_date', 'file_modified_date']:
                    if hasattr(track, date_field):
                        date_str = getattr(track, date_field)
                        if date_str:
                            try:
                                if date_str.startswith('UTC '):
                                    date_str = date_str[4:]
                                return datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M:%S')
                            except Exception as e:
                                logger.debug(f"解析通用日期失败 {date_field}={date_str}: {e}")
                                continue

    except Exception as e:
        logger.warning(f"读取视频元数据失败 {file_path.name}: {e}")

    # Fallback: 使用文件修改时间
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime)
    except Exception as e:
        logger.error(f"获取视频时间失败 {file_path}: {e}")
        return None
