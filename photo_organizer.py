#!/usr/bin/env python3
"""
照片整理工具
功能：将NAS挂载在Windows中的大量照片和视频按年/月目录结构整理
特性：
- 支持断点续传
- 文件哈希去重（使用快速采样哈希，性能优化）
- 完善的错误处理和日志记录
- 进度显示
- 支持 Live Photos 配对处理（保持配对关系）
- 支持视频整理（自动识别拍摄时间）
- 支持暂停/恢复
- 分批处理
- NAS健康度检查
- 执行前确认
"""

import os
import sys
import shutil
import logging
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, asdict

from enum import Enum
import time
import signal
import atexit
import random

# 元数据读取库
try:
    import piexif
    PIEXIF_AVAILABLE = True
except ImportError:
    PIEXIF_AVAILABLE = False

try:
    from pymediainfo import MediaInfo
    MEDIAINFO_AVAILABLE = True
except ImportError:
    MEDIAINFO_AVAILABLE = False

# 检测是否包含中文字符
def contains_chinese(text: str) -> bool:
    """
    检测文本是否包含中文字符

    Args:
        text: 要检测的文本

    Returns:
        是否包含中文字符
    """
    return any('\u4e00' <= char <= '\u9fff' for char in text)

# 支持的图片格式
SUPPORTED_IMAGE_FORMATS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
    '.webp', '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw',
    '.dng', '.orf', '.rw2', '.pef', '.srw', '.x3f', '.3fr',
    '.mos', '.mrw', '.erf', '.k25', '.kdc'
}

# 支持的视频格式
SUPPORTED_VIDEO_FORMATS = {
    '.mp4', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.3gp',
    '.webm', '.m4v', '.ts', '.mts', '.m2ts', '.dv', '.rm', '.rmvb'
}

# Live Photos 的视频格式（MOV 是 iOS Live Photos 的标准格式）
LIVE_PHOTO_VIDEO_FORMATS = {'.mov'}

# 所有支持的媒体格式
SUPPORTED_MEDIA_FORMATS = SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS

# 状态文件
STATUS_FILE = 'organizer_status.json'
LOG_FILE = 'photo_organizer.log'
DUPLICATE_HASHES_FILE = 'duplicate_hashes.json'
SCAN_RESULTS_FILE = 'scan_results.json'
RENAMING_INDEX_FILE = 'renaming_index.json'

# 默认批次配置
DEFAULT_BATCH_SIZE = 100
DEFAULT_BATCH_INTERVAL = 5  # 秒
MAX_RETRIES = 3


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


class PhotoOrganizer:
    """照片整理器"""

    def __init__(self, source_dir: str, target_dir: str, dry_run: bool = False,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 batch_interval: int = DEFAULT_BATCH_INTERVAL):
        """
        初始化整理器

        Args:
            source_dir: 源照片目录
            target_dir: 目标整理目录
            dry_run: 是否仅模拟运行
            batch_size: 批次处理大小
            batch_interval: 批次间间隔（秒）
        """
        self.source_dir = Path(source_dir).resolve()
        self.target_dir = Path(target_dir).resolve()
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.batch_interval = batch_interval

        # 验证目录
        if not self.source_dir.exists():
            raise ValueError(f"源目录不存在: {self.source_dir}")

        # 创建目标目录
        self.target_dir.mkdir(parents=True, exist_ok=True)

        # 初始化状态
        self.status_data: Dict[str, FileRecord] = {}
        self.duplicate_hashes: Set[str] = set()
        self.paused = False
        self.stop_requested = False

        # 重命名索引（格式：YYYYMMDD -> 计数器）
        self.renaming_index: Dict[str, int] = {}

        # 统计信息
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped_duplicate': 0,
            'processing': 0,
            'live_photo_pairs': 0,
            'video_files': 0
        }

        # 设置日志
        self._setup_logging()

        # 设置信号处理
        self._setup_signal_handlers()

        # 注册退出处理
        atexit.register(self._on_exit)

    def _setup_logging(self):
        """设置日志"""
        log_dir = self.target_dir / 'logs'
        log_dir.mkdir(exist_ok=True)

        log_path = log_dir / LOG_FILE

        # 配置日志格式
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_path, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )

        self.logger = logging.getLogger('PhotoOrganizer')
        self.logger.info(f"照片整理器启动")
        self.logger.info(f"源目录: {self.source_dir}")
        self.logger.info(f"目标目录: {self.target_dir}")
        self.logger.info(f"模拟运行: {self.dry_run}")

    def _setup_signal_handlers(self):
        """设置信号处理器"""
        def signal_handler(signum, frame):
            self.logger.warning(f"接收到信号 {signum}, 请求停止...")
            self.stop_requested = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _on_exit(self):
        """退出处理"""
        if self.status_data:
            self._save_status()
        self.logger.info(f"程序退出 - 成功: {self.stats['success']}, "
                        f"失败: {self.stats['failed']}, "
                        f"跳过重复: {self.stats['skipped_duplicate']}")

    def _check_directory_health(self, check_dir: Path, dir_name: str = "目录") -> Dict[str, any]:
        """
        检查指定目录的健康度

        Args:
            check_dir: 要检查的目录
            dir_name: 目录名称（用于日志）

        Returns:
            健康度检查结果
        """
        health_status = {
            'mount_accessible': False,
            'mount_writable': False,
            'response_time_ms': 0,
            'total_size_gb': 0,
            'free_space_gb': 0,
            'issues': []
        }

        try:
            start_time = time.time()

            # 检查挂载点是否可访问
            if not check_dir.exists():
                health_status['issues'].append(f"{dir_name}不存在或无法访问")
            else:
                health_status['mount_accessible'] = True

            # 检查可写性（仅在目标目录检查）
            if dir_name == "目标目录":
                test_file = check_dir / '.health_check'
                try:
                    test_file.write_text('test', encoding='utf-8')
                    test_file.unlink()
                    health_status['mount_writable'] = True
                except Exception as e:
                    health_status['issues'].append(f"无法写入测试文件: {e}")

            # 获取磁盘空间
            if health_status['mount_accessible']:
                try:
                    usage = shutil.disk_usage(check_dir)
                    health_status['total_size_gb'] = usage.total / (1024**3)
                    health_status['free_space_gb'] = usage.free / (1024**3)

                    if health_status['free_space_gb'] < 5:
                        health_status['issues'].append(
                            f"{dir_name}可用空间不足: {health_status['free_space_gb']:.2f}GB"
                        )
                except Exception as e:
                    health_status['issues'].append(f"无法获取磁盘空间信息: {e}")

            response_time = (time.time() - start_time) * 1000
            health_status['response_time_ms'] = response_time

            if response_time > 5000:
                health_status['issues'].append(f"{dir_name}响应时间过长: {response_time:.0f}ms")

        except Exception as e:
            health_status['issues'].append(f"{dir_name}健康检查异常: {e}")

        return health_status

    def check_nas_health(self) -> Dict[str, any]:
        """
        检查NAS健康度（源目录和目标目录）

        Returns:
            健康度检查结果
        """
        self.logger.info("检查NAS健康度...")

        health_status = {
            'source': self._check_directory_health(self.source_dir, "源目录"),
            'target': self._check_directory_health(self.target_dir, "目标目录"),
            'overall_ok': False
        }

        # 判断整体健康状态
        health_status['overall_ok'] = (
            health_status['source']['mount_accessible'] and
            health_status['target']['mount_accessible'] and
            health_status['target']['mount_writable'] and
            health_status['target']['free_space_gb'] > 5  # 保留至少5GB冗余
        )

        # 打印健康度结果
        self.logger.info("\n源目录健康度:")
        self.logger.info(f"  可访问: {health_status['source']['mount_accessible']}")
        self.logger.info(f"  响应时间: {health_status['source']['response_time_ms']:.0f}ms")
        self.logger.info(f"  总空间: {health_status['source']['total_size_gb']:.2f}GB")
        self.logger.info(f"  可用空间: {health_status['source']['free_space_gb']:.2f}GB")

        self.logger.info("\n目标目录健康度:")
        self.logger.info(f"  可访问: {health_status['target']['mount_accessible']}")
        self.logger.info(f"  可写: {health_status['target']['mount_writable']}")
        self.logger.info(f"  响应时间: {health_status['target']['response_time_ms']:.0f}ms")
        self.logger.info(f"  总空间: {health_status['target']['total_size_gb']:.2f}GB")
        self.logger.info(f"  可用空间: {health_status['target']['free_space_gb']:.2f}GB")

        # 汇总所有问题
        all_issues = health_status['source']['issues'] + health_status['target']['issues']
        if all_issues:
            self.logger.warning("检测到问题:")
            for issue in all_issues:
                self.logger.warning(f"  - {issue}")

        return health_status

    def print_scan_summary(self, photo_files: List[Path]):
        """
        打印扫描结果摘要

        Args:
            photo_files: 扫描到的照片文件列表
        """
        total_files = len(photo_files)
        total_size = 0
        format_counts = {}
        year_month_counts = {}

        self.logger.info("分析文件信息...")

        for file_path in photo_files:
            try:
                file_size = file_path.stat().st_size
                total_size += file_size

                ext = file_path.suffix.lower()
                format_counts[ext] = format_counts.get(ext, 0) + 1

                # 尝试获取日期进行统计
                photo_date = self._get_photo_date(file_path)
                if photo_date:
                    year_month = f"{photo_date.strftime('%Y')}/{photo_date.strftime('%m')}"
                    year_month_counts[year_month] = year_month_counts.get(year_month, 0) + 1
            except Exception:
                continue

        # 打印统计信息
        self.logger.info("\n" + "=" * 60)
        self.logger.info("扫描结果摘要")
        self.logger.info("=" * 60)
        self.logger.info(f"总文件数: {total_files:,}")
        self.logger.info(f"总大小: {total_size / (1024**3):.2f} GB")
        self.logger.info(f"平均文件大小: {total_size / total_files / 1024 if total_files > 0 else 0:.2f} KB")

        self.logger.info("\n文件格式分布:")
        for ext, count in sorted(format_counts.items(), key=lambda x: -x[1])[:10]:
            self.logger.info(f"  {ext.ljust(8)}: {count:,} ({count/total_files*100:.1f}%)")

        self.logger.info(f"\n时间分布 (前10个):")
        for ym, count in sorted(year_month_counts.items())[:10]:
            self.logger.info(f"  {ym}: {count:,}")

        self.logger.info(f"\n批次处理配置:")
        batches = (total_files + self.batch_size - 1) // self.batch_size
        self.logger.info(f"  批次大小: {self.batch_size}")
        self.logger.info(f"  批次间间隔: {self.batch_interval}秒")
        self.logger.info(f"  总批次数: {batches}")
        estimated_time = batches * self.batch_interval  # 仅间隔时间
        self.logger.info(f"  预计间隔时间: {estimated_time/60:.1f}分钟")

        self.logger.info("=" * 60)

    def wait_for_confirmation(self) -> bool:
        """
        等待用户确认

        Returns:
            用户是否确认继续
        """
        print("\n" + "=" * 60)
        print("请确认是否开始处理")
        print("=" * 60)
        print("输入 'yes' 或 'y' 继续，其他任何输入将退出")

        try:
            response = input("> ").strip().lower()
            return response in ['yes', 'y']
        except (EOFError, KeyboardInterrupt):
            return False

    def _load_status(self) -> bool:
        """
        加载状态文件并清理无效记录

        Returns:
            是否成功加载
        """
        status_path = self.target_dir / 'logs' / STATUS_FILE
        hash_path = self.target_dir / 'logs' / DUPLICATE_HASHES_FILE

        loaded = False
        removed_count = 0

        if status_path.exists():
            try:
                with open(status_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        record = FileRecord(**value)

                        # 检查记录的有效性
                        source_path = Path(record.source_path)

                        # 如果状态是SUCCESS但源文件不存在，说明文件已被移动或删除
                        if record.status == FileProcessingStatus.SUCCESS.value:
                            if not source_path.exists():
                                # 这是一个有效的成功记录（源文件已移动到目标位置）
                                self.status_data[key] = record
                            else:
                                # 源文件还存在，但状态显示已成功，可能是之前处理异常
                                # 需要重新处理，不加载该记录
                                self.logger.warning(
                                    f"检测到不一致记录: {source_path} 状态为成功但文件仍存在，将重新处理"
                                )
                                removed_count += 1
                        elif record.status == FileProcessingStatus.FAILED.value:
                            # 失败的记录，如果源文件不存在则清理
                            if not source_path.exists():
                                self.logger.debug(
                                    f"清理无效失败记录: {source_path} 不存在"
                                )
                                removed_count += 1
                            else:
                                self.status_data[key] = record
                        else:
                            # 其他状态（PROCESSING, SKIPPED_DUPLICATE等），正常加载
                            self.status_data[key] = record

                self.logger.info(f"从状态文件加载了 {len(self.status_data)} 条记录")
                if removed_count > 0:
                    self.logger.info(f"清理了 {removed_count} 条无效记录")
                loaded = True
            except Exception as e:
                self.logger.error(f"加载状态文件失败: {e}")

        if hash_path.exists():
            try:
                with open(hash_path, 'r', encoding='utf-8') as f:
                    self.duplicate_hashes = set(json.load(f))
                self.logger.info(f"加载了 {len(self.duplicate_hashes)} 个重复文件哈希")
                loaded = True
            except Exception as e:
                self.logger.error(f"加载哈希文件失败: {e}")

        if index_path.exists():
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    self.renaming_index = json.load(f)
                self.logger.info(f"加载了 {len(self.renaming_index)} 个日期重命名索引")
                loaded = True
            except Exception as e:
                self.logger.error(f"加载重命名索引失败: {e}")

        return loaded

    def _save_status(self):
        """保存状态文件"""
        try:
            log_dir = self.target_dir / 'logs'
            log_dir.mkdir(exist_ok=True)

            status_path = log_dir / STATUS_FILE
            hash_path = log_dir / DUPLICATE_HASHES_FILE
            index_path = log_dir / RENAMING_INDEX_FILE

            # 保存文件状态
            data = {key: asdict(value) for key, value in self.status_data.items()}
            with open(status_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 保存哈希集合
            with open(hash_path, 'w', encoding='utf-8') as f:
                json.dump(list(self.duplicate_hashes), f, ensure_ascii=False)

            # 保存重命名索引
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(self.renaming_index, f, ensure_ascii=False)

            self.logger.debug("状态已保存")
        except Exception as e:
            self.logger.error(f"保存状态失败: {e}")

    def _calculate_file_hash(self, file_path: Path, chunk_size: int = 8192) -> Optional[str]:
        """
        计算文件哈希值（使用快速采样法）

        对于小文件（<1MB）计算完整哈希
        对于大文件，只采样文件的开头、中间、结尾部分进行哈希

        Args:
            file_path: 文件路径
            chunk_size: 采样块大小（默认 8KB）

        Returns:
            文件的SHA256哈希值（快速采样版）
        """
        try:
            file_size = file_path.stat().st_size
            sha256_hash = hashlib.sha256()

            # 小文件（<1MB）：计算完整哈希
            if file_size < 1024 * 1024:
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(chunk_size), b""):
                        sha256_hash.update(chunk)
                return sha256_hash.hexdigest()

            # 大文件：快速采样哈希
            with open(file_path, "rb") as f:
                # 1. 读取开头部分
                f.seek(0)
                start_chunk = f.read(chunk_size)
                sha256_hash.update(start_chunk)

                # 2. 读取中间部分
                f.seek(file_size // 2)
                middle_chunk = f.read(chunk_size)
                sha256_hash.update(middle_chunk)

                # 3. 读取结尾部分
                f.seek(max(0, file_size - chunk_size))
                end_chunk = f.read(chunk_size)
                sha256_hash.update(end_chunk)

                # 4. 将文件大小也加入哈希（避免不同大小的文件得到相同采样）
                sha256_hash.update(str(file_size).encode('utf-8'))

            return sha256_hash.hexdigest()

        except Exception as e:
            self.logger.error(f"计算文件哈希失败 {file_path}: {e}")
            return None

    def _is_live_photo_file(self, file_path: Path) -> bool:
        """
        判断是否是 Live Photos 文件

        Args:
            file_path: 文件路径

        Returns:
            是否是 Live Photos 文件
        """
        ext = file_path.suffix.lower()

        # MOV 文件可能是 Live Photos 视频部分
        if ext in LIVE_PHOTO_VIDEO_FORMATS:
            return True

        # 某些格式的图片也可能是 Live Photos
        if ext in {'.heic', '.heif', '.jpg', '.jpeg'}:
            # 检查是否存在对应的 MOV 文件
            pair_path = self._find_live_photo_pair(file_path)
            return pair_path is not None

        return False

    def _find_live_photo_pair(self, file_path: Path) -> Optional[Path]:
        """
        查找 Live Photos 配对文件

        Args:
            file_path: 文件路径

        Returns:
            配对文件路径，如果不存在则返回 None
        """
        ext = file_path.suffix.lower()
        stem = file_path.stem  # 文件名（不含扩展名）

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

    def _get_video_creation_date(self, file_path: Path) -> Optional[datetime]:
        """
        获取视频的拍摄时间（通过 MediaInfo 读取元数据）

        Args:
            file_path: 视频文件路径

        Returns:
            视频的拍摄时间
        """
        if not MEDIAINFO_AVAILABLE:
            self.logger.warning(f"pymediainfo 未安装，无法读取视频元数据，使用文件修改时间: {file_path.name}")
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
                                # MediaInfo 返回的日期格式通常是: "UTC 2024-01-01 12:00:00"
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
                                    self.logger.debug(f"解析视频日期失败 {date_field}={date_str}: {e}")
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
                                    self.logger.debug(f"解析通用日期失败 {date_field}={date_str}: {e}")
                                    continue

        except Exception as e:
            self.logger.warning(f"读取视频元数据失败 {file_path.name}: {e}")

        # Fallback: 使用文件修改时间
        try:
            return datetime.fromtimestamp(file_path.stat().st_mtime)
        except Exception as e:
            self.logger.error(f"获取视频时间失败 {file_path}: {e}")
            return None

    def _get_image_exif_date(self, file_path: Path) -> Optional[datetime]:
        """
        从照片的 EXIF 数据中获取拍摄时间

        Args:
            file_path: 照片文件路径

        Returns:
            照片的拍摄时间，如果无法获取则返回 None
        """
        if not PIEXIF_AVAILABLE:
            self.logger.debug(f"piexif 未安装，无法读取 EXIF: {file_path.name}")
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
                    self.logger.debug(f"解析 EXIF {tag} 失败: {e}")
                    continue

        except Exception as e:
            # 不是所有图片都有 EXIF，这是正常的
            self.logger.debug(f"读取 EXIF 失败 {file_path.name}: {e}")

        return None

    def _get_photo_date(self, file_path: Path) -> Optional[datetime]:
        """
        获取照片/视频的拍摄日期

        优先级：
        1. 文件名中的日期
        2. 照片的 EXIF 拍摄时间
        3. 视频的元数据时间
        4. 文件修改时间（fallback）

        Args:
            file_path: 文件路径

        Returns:
            照片/视频的拍摄日期，如果无法获取则返回文件的修改时间
        """
        ext = file_path.suffix.lower()

        # 1. 首先尝试从文件名解析日期
        date_from_name = self._parse_date_from_filename(file_path)
        if date_from_name:
            return date_from_name

        # 2. 如果是照片文件，尝试从 EXIF 读取
        if ext in SUPPORTED_IMAGE_FORMATS:
            exif_date = self._get_image_exif_date(file_path)
            if exif_date:
                return exif_date

        # 3. 如果是视频文件，使用视频时间获取方法
        if ext in SUPPORTED_VIDEO_FORMATS:
            return self._get_video_creation_date(file_path)

        # 4. Fallback: 尝试从文件修改时间获取
        try:
            mod_time = datetime.fromtimestamp(file_path.stat().st_mtime)
            return mod_time
        except Exception as e:
            self.logger.error(f"获取文件时间失败 {file_path}: {e}")
            return None

    def _parse_date_from_filename(self, file_path: Path) -> Optional[datetime]:
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
                    return datetime.strptime(part, fmt)
                except ValueError:
                    continue

        return None

    def _get_target_directory(self, photo_date: datetime) -> Path:
        """
        根据照片日期生成目标目录

        Args:
            photo_date: 照片日期

        Returns:
            目标目录路径 (年/月)
        """
        year = photo_date.strftime('%Y')
        month = photo_date.strftime('%m')
        return self.target_dir / year / month

    def _get_new_filename(self, file_path: Path, photo_date: datetime) -> str:
        """
        获取新文件名（仅对非中文文件名）

        格式：IMG_YYYYMMDD_XXXX.ext 或 MOV_YYYYMMDD_XXXX.ext

        Args:
            file_path: 原始文件路径
            photo_date: 照片/视频日期

        Returns:
            新文件名，如果原文件名包含中文则返回原文件名
        """
        original_name = file_path.stem
        ext = file_path.suffix

        # 如果文件名包含中文，保留原文件名
        if contains_chinese(original_name):
            return file_path.name

        # 确定前缀
        ext_lower = ext.lower()
        if ext_lower in SUPPORTED_VIDEO_FORMATS:
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

    def _get_unique_filename(self, target_dir: Path, original_filename: str) -> str:
        """
        生成唯一的文件名，避免重复

        Args:
            target_dir: 目标目录
            original_filename: 原始文件名

        Returns:
            唯一的文件名
        """
        target_path = target_dir / original_filename

        if not target_path.exists():
            return original_filename

        # 文件已存在，添加序号
        name, ext = os.path.splitext(original_filename)
        counter = 1

        while True:
            new_filename = f"{name}_{counter}{ext}"
            new_path = target_dir / new_filename
            if not new_path.exists():
                return new_filename
            counter += 1



    def _verify_file_integrity(self, source: Path, target: Path) -> bool:
        """
        校验文件复制后的完整性

        对于小文件（<10MB）：只校验文件大小
        对于大文件（>=10MB）：文件大小 + 快速采样哈希校验

        Args:
            source: 源文件路径
            target: 目标文件路径

        Returns:
            完整性校验是否通过
        """
        try:
            # 方法1：检查文件大小
            source_size = source.stat().st_size
            target_size = target.stat().st_size

            if source_size != target_size:
                self.logger.error(
                    f"文件大小不匹配: {source_size} vs {target_size}"
                )
                return False

            # 方法2：快速哈希校验（仅对大文件）
            if source_size >= 10 * 1024 * 1024:  # 大于等于10MB的文件做快速校验
                source_hash = self._calculate_file_hash(source)
                target_hash = self._calculate_file_hash(target)

                if source_hash != target_hash:
                    self.logger.error("文件哈希不匹配")
                    return False

            return True

        except Exception as e:
            self.logger.error(f"完整性校验异常: {e}")
            return False

    def _move_file(self, source: Path, target: Path) -> bool:
        """
        安全移动文件（带完整性校验）

        流程：
        1. 先复制文件
        2. 校验复制后的文件完整性
        3. 仅校验通过后删除源文件

        Args:
            source: 源文件路径
            target: 目标文件路径

        Returns:
            是否成功
        """
        if self.dry_run:
            self.logger.info(f"[模拟] 将 {source} 移动到 {target}")
            return True

        try:
            # 确保目标目录存在
            target.parent.mkdir(parents=True, exist_ok=True)

            # 步骤1：先复制文件（保留元数据）
            shutil.copy2(str(source), str(target))

            # 步骤2：校验复制后的文件完整性
            if self._verify_file_integrity(source, target):
                # 步骤3：仅校验通过后删除源文件
                source.unlink()
                return True
            else:
                # 校验失败，删除不完整的目标文件
                if target.exists():
                    target.unlink(missing_ok=True)
                self.logger.error(f"文件复制后完整性校验失败: {source}")
                return False

        except Exception as e:
            # 异常时清理目标文件（避免残留不完整文件）
            if target.exists():
                target.unlink(missing_ok=True)
            self.logger.error(f"移动文件失败 {source} -> {target}: {e}")
            return False

    def _find_photo_files(self) -> List[Path]:
        """
        查找所有照片和视频文件

        Returns:
            媒体文件路径列表
        """
        media_files = []
        scanned_count = 0

        self.logger.info("开始扫描媒体文件（照片和视频）...")

        try:
            for root, dirs, files in os.walk(self.source_dir):
                if self.stop_requested:
                    break

                for filename in files:
                    if self.stop_requested:
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

    def _process_single_file(self, file_path: Path, target_path: Path) -> bool:
        """
        处理单个文件（内部方法）

        Args:
            file_path: 源文件路径
            target_path: 目标文件路径

        Returns:
            是否处理成功
        """
        file_key = str(file_path)

        # 检查是否已处理
        if file_key in self.status_data:
            record = self.status_data[file_key]
            if record.status == FileProcessingStatus.SUCCESS.value:
                self.logger.debug(f"文件已处理: {file_path}")
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
            file_hash = self._calculate_file_hash(file_path)
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
                self.stats['skipped_duplicate'] += 1
                return True

            # 移动文件
            if self._move_file(file_path, target_path):
                record.status = FileProcessingStatus.SUCCESS.value
                record.processed_at = datetime.now().isoformat()
                self.duplicate_hashes.add(file_hash)
                self.status_data[file_key] = record

                self.logger.debug(f"成功: {file_path} -> {target_path}")
                self.stats['success'] += 1
                return True
            else:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "移动文件失败"
                self.status_data[file_key] = record
                self.stats['failed'] += 1
                return False

        except Exception as e:
            self.logger.error(f"处理文件异常 {file_path}: {e}")
            record.status = FileProcessingStatus.FAILED.value
            record.error_message = str(e)
            self.status_data[file_key] = record
            self.stats['failed'] += 1
            return False

    def _process_live_photo_pair(self, photo_file: Path, video_file: Path) -> bool:
        """
        处理 Live Photos 配对文件

        Args:
            photo_file: 照片文件路径
            video_file: 视频文件路径

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
        target_dir = self._get_target_directory(photo_date)

        # 生成新的文件名对
        # 先用照片文件获取新名称，然后提取序号部分给视频使用
        new_photo_name = self._get_new_filename(photo_file, photo_date)

        # 检查照片文件名是否包含中文（包含中文则保留原样）
        if contains_chinese(photo_file.stem):
            # 中文文件名，视频用相同的基础名
            photo_base_name = photo_file.stem
            video_target_filename = f"{photo_base_name}{video_file.suffix}"
            photo_target_filename = new_photo_name
        else:
            # 英文文件名，生成配对的文件名
            # 格式：IMG_YYYYMMDD_XXXX.heic 和 MOV_YYYYMMDD_XXXX.mov
            date_key = photo_date.strftime('%Y%m%d')

            # 获取当前序号（之前加1了，这里减1）
            sequence = self.renaming_index[date_key] - 1
            sequence_str = f"{sequence:04d}"

            photo_target_filename = f"IMG_{date_key}_{sequence_str}{photo_file.suffix}"
            video_target_filename = f"MOV_{date_key}_{sequence_str}{video_file.suffix}"

        # 处理两个文件
        success = True

        # 处理照片
        photo_target = target_dir / photo_target_filename
        if not self._process_single_file(photo_file, photo_target):
            self.logger.error(f"Live Photos 照片处理失败: {photo_file}")
            success = False

        # 处理视频
        video_target = target_dir / video_target_filename
        if not self._process_single_file(video_file, video_target):
            self.logger.error(f"Live Photos 视频处理失败: {video_file}")
            success = False

        if success:
            self.logger.info(f"Live Photos 成功: {photo_target} + {video_target}")
            self.stats['live_photo_pairs'] += 1

        return success

    def _process_file(self, file_path: Path) -> bool:
        """
        处理单个媒体文件（照片或视频）

        Args:
            file_path: 文件路径

        Returns:
            是否处理成功
        """
        file_key = str(file_path)

        # 检查是否已处理
        if file_key in self.status_data:
            record = self.status_data[file_key]
            if record.status == FileProcessingStatus.SUCCESS.value:
                self.logger.debug(f"文件已处理: {file_path}")
                self.stats['success'] += 1
                return True
            elif record.status == FileProcessingStatus.SKIPPED_DUPLICATE.value:
                self.stats['skipped_duplicate'] += 1
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
            return self._process_live_photo_pair(file_path, pair_file)

        # 普通文件处理
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
            file_hash = self._calculate_file_hash(file_path)
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
                self.stats['skipped_duplicate'] += 1
                return True

            # 获取日期
            media_date = self._get_photo_date(file_path)
            if not media_date:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "无法获取日期"
                self.status_data[file_key] = record
                return False

            # 获取目标目录
            target_dir = self._get_target_directory(media_date)

            # 生成新文件名（英文名自动重命名，中文名保留）
            new_filename = self._get_new_filename(file_path, media_date)

            # 如果是中文文件名，仍然需要检查重名
            if contains_chinese(file_path.stem):
                target_filename = self._get_unique_filename(target_dir, new_filename)
            else:
                target_filename = new_filename

            target_path = target_dir / target_filename

            record.target_path = str(target_path)

            # 移动文件
            if self._move_file(file_path, target_path):
                record.status = FileProcessingStatus.SUCCESS.value
                record.processed_at = datetime.now().isoformat()
                self.duplicate_hashes.add(file_hash)
                self.status_data[file_key] = record

                file_type = "视频" if file_path.suffix.lower() in SUPPORTED_VIDEO_FORMATS else "照片"
                self.logger.info(f"成功 ({file_type}): {file_path} -> {target_path}")
                self.stats['success'] += 1

                if file_type == "视频":
                    self.stats['video_files'] += 1

                return True
            else:
                record.status = FileProcessingStatus.FAILED.value
                record.error_message = "移动文件失败"
                self.status_data[file_key] = record
                self.stats['failed'] += 1
                return False

        except Exception as e:
            self.logger.error(f"处理文件异常 {file_path}: {e}")
            record.status = FileProcessingStatus.FAILED.value
            record.error_message = str(e)
            self.status_data[file_key] = record
            self.stats['failed'] += 1
            return False

    def _process_file_with_retry(self, file_path: Path) -> bool:
        """
        带重试的文件处理

        Args:
            file_path: 照片文件路径

        Returns:
            是否处理成功
        """
        for attempt in range(MAX_RETRIES):
            try:
                return self._process_file(file_path)
            except Exception as e:
                self.logger.warning(f"处理文件失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {file_path}")
                if attempt < MAX_RETRIES - 1:
                    wait_time = (attempt + 1) * 2  # 指数退避
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                else:
                    self.logger.error(f"文件处理失败，已达最大重试次数: {file_path}")
                    file_key = str(file_path)
                    if file_key in self.status_data:
                        self.status_data[file_key].status = FileProcessingStatus.FAILED.value
                        self.status_data[file_key].error_message = f"重试{MAX_RETRIES}次后仍失败"
                        self.stats['failed'] += 1
        return False

    def organize(self):
        """执行照片整理"""
        self.logger.info("=" * 60)
        self.logger.info("开始照片整理")
        self.logger.info("=" * 60)

        # 第一步：NAS健康度检查
        self.logger.info("\n【第一步】NAS健康度检查")
        health_status = self.check_nas_health()
        if not health_status['source']['mount_accessible']:
            self.logger.error("NAS挂载点不可访问，无法继续")
            return

        # 第二步：扫描照片文件
        self.logger.info("\n【第二步】扫描照片文件")
        try:
            photo_files = self._find_photo_files()
        except Exception as e:
            self.logger.error(f"查找照片文件失败: {e}")
            return

        self.stats['total'] = len(photo_files)
        if self.stats['total'] == 0:
            self.logger.warning("没有找到照片文件")
            return

        # 第三步：显示扫描摘要并等待确认
        self.logger.info("\n【第三步】分析并确认处理计划")
        self.print_scan_summary(photo_files)

        if not self.wait_for_confirmation():
            self.logger.info("用户取消操作")
            return

        # 第四步：加载之前的状态
        self.logger.info("\n【第四步】准备处理")
        if self._load_status():
            self.logger.info("检测到之前的状态，将尝试继续...")

        # 过滤已处理的文件
        remaining_files = []
        for file_path in photo_files:
            file_key = str(file_path)
            if file_key not in self.status_data or \
               self.status_data[file_key].status != FileProcessingStatus.SUCCESS.value:
                remaining_files.append(file_path)

        self.logger.info(f"剩余需要处理的文件: {len(remaining_files)}")

        if len(remaining_files) == 0:
            self.logger.info("所有文件已处理完成")
            return

        # 第五步：分批处理
        self.logger.info("\n【第五步】开始分批处理")
        self.logger.info(f"批次大小: {self.batch_size}")
        self.logger.info(f"批次间间隔: {self.batch_interval}秒")

        total_batches = (len(remaining_files) + self.batch_size - 1) // self.batch_size
        start_time = time.time()
        last_save_time = start_time

        for batch_num in range(total_batches):
            if self.stop_requested:
                self.logger.warning("用户请求停止，正在保存状态...")
                break

            batch_start = batch_num * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(remaining_files))
            batch_files = remaining_files[batch_start:batch_end]

            self.logger.info(f"\n处理批次 {batch_num + 1}/{total_batches} "
                           f"(文件 {batch_start + 1}-{batch_end})")

            # 批次前健康度检查
            if batch_num > 0:  # 第一批次已经在前面检查过了
                self.logger.info("批次间NAS健康度检查...")
                batch_health = self.check_nas_health()
                if not batch_health['source']['mount_accessible']:
                    self.logger.error("NAS不可访问，等待恢复...")
                    time.sleep(self.batch_interval)
                    continue

            # 处理批次中的文件
            batch_start_time = time.time()
            batch_success = 0
            batch_failed = 0

            for index, file_path in enumerate(batch_files, 1):
                if self.stop_requested:
                    break

                # 显示进度
                if index % 10 == 0 or index == len(batch_files):
                    elapsed = time.time() - batch_start_time
                    rate = index / elapsed if elapsed > 0 else 0

                    self.logger.info(
                        f"  批次进度: {index}/{len(batch_files)} "
                        f"({index/len(batch_files)*100:.1f}%) | "
                        f"速度: {rate:.1f}个/秒"
                    )

                # 处理文件
                if self._process_file_with_retry(file_path):
                    batch_success += 1
                else:
                    batch_failed += 1

            # 批次处理完成
            batch_elapsed = time.time() - batch_start_time
            self.logger.info(f"批次完成 - 耗时: {batch_elapsed:.1f}秒, "
                           f"成功: {batch_success}, 失败: {batch_failed}")

            # 定期保存状态
            current_time = time.time()
            if current_time - last_save_time > 60:  # 每分钟保存一次
                self._save_status()
                last_save_time = current_time

            # 批次间等待（最后一批不需要等待）
            if batch_num < total_batches - 1 and not self.stop_requested:
                self.logger.info(f"等待 {self.batch_interval} 秒后处理下一批次...")
                time.sleep(self.batch_interval)

        # 最终保存状态
        self._save_status()

        # 显示最终统计
        elapsed = time.time() - start_time
        self.logger.info("\n" + "=" * 60)
        self.logger.info("照片整理完成")
        self.logger.info(f"总用时: {elapsed/60:.1f} 分钟")
        self.logger.info(f"总文件数: {self.stats['total']}")
        self.logger.info(f"成功: {self.stats['success']}")
        self.logger.info(f"失败: {self.stats['failed']}")
        self.logger.info(f"跳过重复: {self.stats['skipped_duplicate']}")
        self.logger.info(f"平均速度: {self.stats['total']/elapsed:.1f} 个/秒")
        self.logger.info("=" * 60)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description='照片整理工具 - 将照片按年/月目录结构整理',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 整理照片
  python photo_organizer.py Z:/nas_photos D:/organized_photos

  # 模拟运行（不实际移动文件）
  python photo_organizer.py Z:/nas_photos D:/organized_photos --dry-run

  # 自定义批次配置
  python photo_organizer.py Z:/nas_photos D:/organized_photos --batch-size 50 --batch-interval 10

  # 跳过确认（自动化场景）
  python photo_organizer.py Z:/nas_photos D:/organized_photos --no-confirm
        """
    )

    parser.add_argument(
        'source_dir',
        default='E:\相册&视频',
        help='源照片目录路径'
    )

    parser.add_argument(
        'target_dir',
        default='E:\相册&视频-整理',
        help='目标整理目录路径'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='模拟运行，不实际移动文件'
    )

    parser.add_argument(
        '--batch-size',
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f'每批处理的文件数量 (默认: {DEFAULT_BATCH_SIZE})'
    )

    parser.add_argument(
        '--batch-interval',
        type=int,
        default=DEFAULT_BATCH_INTERVAL,
        help=f'批次间间隔秒数 (默认: {DEFAULT_BATCH_INTERVAL})'
    )

    parser.add_argument(
        '--no-confirm',
        action='store_true',
        help='跳过确认提示'
    )

    args = parser.parse_args()

    try:
        organizer = PhotoOrganizer(
            source_dir=args.source_dir,
            target_dir=args.target_dir,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            batch_interval=args.batch_interval
        )

        # 跳过确认选项
        if args.no_confirm:
            # 重写确认方法
            organizer.wait_for_confirmation = lambda: True

        organizer.organize()
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
