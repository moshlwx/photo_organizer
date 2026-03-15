"""
文件处理器 - 处理单个文件和 Live Photos 配对
"""
import logging
from pathlib import Path
from typing import Optional, Dict, Set
from datetime import datetime as dt

from src.models.file_record import FileRecord, FileProcessingStatus, ProcessingStats
from src.utils.hash_utils import calculate_file_hash
from src.utils.file_utils import move_file_safe, verify_file_integrity, get_unique_filename
from src.utils.naming_utils import FileNamer, contains_chinese
from src.metadata.exif_reader import get_image_exif_date
from src.metadata.media_info import get_video_creation_date
from config.settings import (
    SUPPORTED_IMAGE_FORMATS, SUPPORTED_VIDEO_FORMATS,
    LIVE_PHOTO_VIDEO_FORMATS, INTEGRITY_CHECK_THRESHOLD
)


class FileProcessor:
    """文件处理器"""

    def __init__(self,
                 status_data: Dict[str, FileRecord],
                 duplicate_hashes: Set[str],
                 file_namer: FileNamer,
                 dry_run: bool = False):
        """
        初始化处理器

        Args:
            status_data: 状态数据字典
            duplicate_hashes: 重复文件哈希集合
            file_namer: 文件命名器
            dry_run: 是否为模拟运行
        """
        self.status_data = status_data
        self.duplicate_hashes = duplicate_hashes
        self.file_namer = file_namer
        self.dry_run = dry_run
        self.logger = logging.getLogger('FileProcessor')
        self.stats = ProcessingStats()

    def _get_photo_date(self, file_path: Path) :
        """
        获取照片/视频的拍摄日期

        优先级：
        1. 文件名中的日期
        2. 照片的 EXIF 拍摄时间
        3. 视频的元数据时间（或文件修改时间）
        4. 文件修改时间（fallback）
        """
        # 从文件名解析日期
        date_from_name = self._parse_date_from_filename(file_path)
        if date_from_name:
            return date_from_name

        ext = file_path.suffix.lower()

        # 如果是照片文件，尝试从 EXIF 读取
        if ext in SUPPORTED_IMAGE_FORMATS:
            exif_date = get_image_exif_date(file_path)
            if exif_date:
                return exif_date

        # 如果是视频文件，使用文件修改时间（跳过元数据读取以提升性能）
        if ext in SUPPORTED_VIDEO_FORMATS:
            try:
                return dt.fromtimestamp(file_path.stat().st_mtime)
            except Exception as e:
                self.logger.error(f"获取视频时间失败 {file_path}: {e}")
                return None

        # Fallback: 尝试从文件修改时间获取
        try:
            return dt.fromtimestamp(file_path.stat().st_mtime)
        except Exception as e:
            self.logger.error(f"获取文件时间失败 {file_path}: {e}")
            return None

    def _parse_date_from_filename(self, file_path: Path):
        """
        从文件名解析日期

        支持常见格式:
        - IMG_20240101_120000.jpg
        - 2024-01-01-12-00-00.jpg
        - 20240101_120000.jpg
        - DSC_20240101_120000.jpg
        """
        filename = file_path.stem

        # 尝试多种日期格式
        date_formats = [
            '%Y%m%d_%H%M%S',      # 20240101_120000
            '%Y-%m-%d-%H-%M-%S',   # 2024-01-01-12-00-00
            '%Y%m%d',              # 20240101
            '%Y-%m-%d',            # 2024-01-01
        ]

        # 处理带前缀的文件名 (如 IMG_2024011...)
        parts = filename.split('_')
        for part in parts:
            for fmt in date_formats:
                try:
                    return dt.strptime(part, fmt)
                except ValueError:
                    continue

        return None

    def _find_live_photo_pair(self, file_path: Path) -> Optional[Path]:
        """
        查找 Live Photos 配对文件

        Args:
            file_path: 文件路径

        Returns:
            配对文件路径，如果不存在则返回 None
        """
        ext = file_path.suffix.lower()
        stem = file_path.stem

        # 如果是 MOV，查找对应的图片
        if ext == '.mov':
            for img_ext in ['.heic', '.heif', '.jpg', '.jpeg']:
                pair_path = file_path.parent / f"{stem}{img_ext}"
                if pair_path.exists():
                    return pair_path

        # 如果是图片，查找对应的 MOV
        elif ext in {'.heic', '.heif', '.jpg', '.jpeg'}:
            mov_path = file_path.parent / f"{stem}.mov"
            if mov_path.exists():
                return mov_path

        return None

    def _process_single_file(self, file_path: Path, target_path: Path,
                           is_video: bool = False) -> bool:
        """
        处理单个文件（内部方法）

        Args:
            file_path: 源文件路径
            target_path: 目标文件路径
            is_video: 是否为视频文件

        Returns:
            是否处理成功
        """
        file_key = str(file_path)

        # 检查是否已处理
        if file_key in self.status_data:
            record = self.status_data[file_key]
            if record.status == FileProcessingStatus.SUCCESS.value:
                self.logger.debug(f"文件已处理={file_path}")
                return True
            elif record.status == FileProcessingStatus.SKIPPED_DUPLICATE.value:
                return True

        # 创建处理记录
        record = FileRecord(
            source_path=file_key,
            target_path=str(target_path),
            status=FileProcessingStatus.PROCESSING.value,
            file_size=file_path.stat().st_size
        )

        try:
            # 检查文件是否可读
            if not file_path.exists():
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "文件不存在"
                self.status_data[file_key] = record
                return False

            # 计算文件哈希
            file_hash = calculate_file_hash(file_path)
            if not file_hash:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "计算哈希失败"
                self.status_data[file_key] = record
                return False

            record.file_hash = file_hash

            # 检查是否重复
            if file_hash in self.duplicate_hashes:
                record.status = FileProcessingStatus.SKIPPED_DUPLICATE.value
                record.error_message = "重复文件"
                self.status_data[file_key] = record
                self.logger.warning(f"跳过重复文件: {file_path}")
                self.stats.skipped_duplicate += 1
                return True

            # 移动文件
            verify_func = lambda s, t: verify_file_integrity(s, t, calculate_file_hash,
                                                        INTEGRITY_CHECK_THRESHOLD)

            if move_file_safe(file_path, target_path, verify_func, self.dry_run, self.logger):
                record.status = FileProcessingStatus.SUCCESS.value
                import datetime
                record.processed_at = dt.now().isoformat()
                self.duplicate_hashes.add(file_hash)
                self.status_data[file_key] = record

                self.logger.debug(f"成功: {file_path} -> {target_path}")
                self.stats.success += 1

                if is_video:
                    self.stats.video_files += 1

                return True
            else:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "移动文件失败"
                self.status_data[file_key] = record
                self.stats.failed += 1
                return False

        except Exception as e:
            self.logger.error(f"处理文件异常 {file_path}: {e}")
            record.status = FileProcessingStatus.FAILED.value
            record.error_message = str(e)
            self.status_data[file_key] = record
            self.stats.failed += 1
            return False

    def process_file(self, file_path: Path, target_dir_func) -> bool:
        """
        处理单个媒体文件（照片或视频）

        Args:
            file_path: 文件路径
            target_dir_func: 获取目标目录的函数（接受日期参数）

        Returns:
            是否处理成功
        """
        file_key = str(file_path)

        # 检查是否已处理
        if file_key in self.status_data:
            record = self.status_data[file_key]
            if record.status == FileProcessingStatus.SUCCESS.value:
                self.logger.debug(f"文件已处理: {file_path}")
                self.stats.success += 1
                return True
            elif record.status == FileProcessingStatus.SKIPPED_DUPLICATE.value:
                self.stats.skipped_duplicate += 1
                return True

        # 检查是否是 Live Photos 配对的一部分
        pair_file = self._find_live_photo_pair(file_path)

        # 如果是 Live Photos，处理配对
        if pair_file:
            # 只处理其中一种类型（避免重复处理）
            # 优先处理图片文件，因为它是主要文件
            is_video = file_path.suffix.lower() == '.mov'
            if is_video:
                # 如果当前是视频，跳过（由图片文件处理配对）
                self.logger.debug(f"跳过 Live Photos 视频部分（将由图片处理）: {file_path}")
                return True

            # 处理 Live Photos 配对（图片+视频）
            return self._process_live_photo_pair(file_path, pair_file, target_dir_func)

        # 普通文件处理
        return self._process_normal_file(file_path, target_dir_func)

    def _process_live_photo_pair(self, photo_file: Path, video_file: Path,
                                 target_dir_func) -> bool:
        """
        处理 Live Photos 配对文件

        Args:
            photo_file: 照片文件路径
            video_file: 视频文件路径
            target_dir_func: 获取目标目录的函数

        Returns:
            是否处理成功
        """
        file_key = str(photo_file)
        video_key = str(video_file)

        # 检查是否已处理
        if file_key in self.status_data and self.status_data[file_key].status == FileProcessingStatus.SUCCESS.value:
            self.logger.debug(f"Live Photos 已处理: {photo_file}")
            return True

        self.logger.info(f"处理 Live Photos 配对: {photo_file.name} + {video_file.name}")

        # 获取照片日期（以照片为准）
        photo_date = self._get_photo_date(photo_file)
        if not photo_date:
            self.logger.error(f"无法获取 Live Photos 日期: {photo_file}")
            return False

        # 获取目标目录
        target_dir = target_dir_func(photo_date)

        # Live Photos 不重命名，保留原文件名
        photo_target_filename = photo_file.name
        video_target_filename = video_file.suffix.startswith('.') and \
                             f"{photo_file.stem}{video_file.suffix}" or video_file.name

        # 检查是否存在，如果存在则添加序号
        photo_target = target_dir / photo_target_filename
        video_target = target_dir / video_target_filename

        counter = 1
        while photo_target.exists() or video_target.exists():
            name, ext = photo_target_filename.rsplit('.', 1)
            photo_target_filename = f"{name}_{counter}.{ext}"

            vname, vext = video_target_filename.rsplit('.', 1)
            video_target_filename = f"{vname}_{counter}.{vext}"

            photo_target = target_dir / photo_target_filename
            video_target = target_dir / video_target_filename
            counter += 1

        # 处理两个文件
        success = True

        # 处理照片
        if not self._process_single_file(photo_file, photo_target, is_video=False):
            self.logger.error(f"Live Photos 照片处理失败: {photo_file}")
            success = False

        # 处理视频
        if not self._process_single_file(video_file, video_target, is_video=True):
            self.logger.error(f"Live Photos 视频处理失败: {video_file}")
            success = False

        if success:
            self.logger.info(f"Live Photos 成功: {photo_target} + {video_target}")
            self.stats.live_photo_pairs += 1

        return success

    def _process_normal_file(self, file_path: Path, target_dir_func) -> bool:
        """
        处理普通文件（非 Live Photos）

        Args:
            file_path: 文件路径
            target_dir_func: 获取目标目录的函数

        Returns:
            是否处理成功
        """
        file_key = str(file_path)

        # 创建处理记录
        record = FileRecord(
            source_path=file_key,
            target_path='',
            status=FileProcessingStatus.PROCESSING.value,
            file_size=file_path.stat().st_size
        )

        try:
            # 检查文件是否可读
            if not file_path.exists():
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "文件不存在"
                self.status_data[file_key] = record
                return False

            # 计算文件哈希
            file_hash = calculate_file_hash(file_path)
            if not file_hash:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "计算哈希失败"
                self.status_data[file_key] = record
                return False

            record.file_hash = file_hash

            # 检查是否重复
            if file_hash in self.duplicate_hashes:
                record.status = FileProcessingStatus.SKIPPED_DUPLICATE.value
                record.error_message = "重复文件"
                self.status_data[file_key] = record
                self.logger.warning(f"跳过重复文件: {file_path}")
                self.stats.skipped_duplicate += 1
                return True

            # 获取日期
            media_date = self._get_photo_date(file_path)
            if not media_date:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "无法获取日期"
                self.status_data[file_key] = record
                return False

            # 获取目标目录
            target_dir = target_dir_func(media_date)

            # 确定是否为视频
            is_video = file_path.suffix.lower() in SUPPORTED_VIDEO_FORMATS

            # 生成新文件名（英文名自动重命名，中文名保留）
            new_filename = self.file_namer.get_new_filename(file_path, media_date, is_video)

            # 如果是中文文件名，仍然需要检查重名
            if contains_chinese(file_path.stem):
                target_filename = get_unique_filename(target_dir, new_filename)
            else:
                target_filename = new_filename

            target_path = target_dir / target_filename
            record.target_path = str(target_path)

            # 移动文件
            verify_func = lambda s, t: verify_file_integrity(s, t, calculate_file_hash,
                                                        INTEGRITY_CHECK_THRESHOLD)

            if move_file_safe(file_path, target_path, verify_func, self.dry_run, self.logger):
                record.status = FileProcessingStatus.SUCCESS.value
                import datetime
                record.processed_at = dt.now().isoformat()
                self.duplicate_hashes.add(file_hash)
                self.status_data[file_key] = record

                file_type = "视频" if is_video else "照片"
                self.logger.info(f"成功 ({file_type}): {file_path} -> {target_path}")
                self.stats.success += 1

                if is_video:
                    self.stats.video_files += 1

                return True
            else:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "移动文件失败"
                self.status_data[file_key] = record
                self.stats.failed += 1
                return False

        except Exception as e:
            self.logger.error(f"处理文件异常 {file_path}: {e}")
            record.status = FileProcessingStatus.FAILED.value
            record.error_message = str(e)
            self.status_data[file_key] = record
            self.stats.failed += 1
            return False

    def get_stats(self) -> ProcessingStats:
        """获取处理统计"""
        return self.stats
