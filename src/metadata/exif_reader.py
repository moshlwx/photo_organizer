"""
EXIF 元数据读取
"""
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

try:
    import piexif
    PIEXIF_AVAILABLE = True
except ImportError:
    PIEXIF_AVAILABLE = False


def get_image_exif_date(file_path: Path) -> Optional[datetime]:
    """
    从照片的 EXIF 数据中获取拍摄时间

    Args:
        file_path: 照片文件路径

    Returns:
        照片的拍摄时间，如果无法获取则返回 None
    """
    logger = logging.getLogger('ExifReader')

    if not PIEXIF_AVAILABLE:
        logger.debug(f"piexif 未安装，无法读取 EXIF: {file_path.name}")
        return None

    try:
        # 读取 EXIF 数据
        exif_dict = piexif.load(str(file_path))

        # 尝试多种 EXIF 标签，按优先级顺序
        exif_tags = [
            'DateTimeOriginal',      # 原始拍摄时间（最准确）
            'DateTimeDigitized',     # 数字化时间
            'DateTime'               # 修改时间
        ]

        for tag in exif_tags:
            try:
                # EXIF 的时间格式: "YYYY:MM:DD HH:MM:SS"
                date_str = exif_dict.get('Exif', {}).get(piexif.ExifIFD[tag])
                if date_str:
                    # 将 "YYYY:MM:DD" 格式转换为 "YYYY-MM-DD"
                    date_str = date_str.replace(':', '-', 2)
                    return datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logger.debug(f"解析 EXIF {tag} 失败: {e}")
                continue

    except Exception as e:
        # 不是所有图片都有 EXIF，这是正常的
        logger.debug(f"读取 EXIF 失败 {file_path.name}: {e}")

    return None
