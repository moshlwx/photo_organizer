"""
照片/视频整理器 - 主类
"""
import os
import sys
import json
import logging
import time
import signal
import atexit
from pathlib import Path
from typing import Dict, Set, List
from datetime import datetime

from src.models.file_record import FileRecord, FileProcessingStatus
from src.core.file_scanner import FileScanner
from src.core.file_processor import FileProcessor
from src.utils.naming_utils import FileNamer
from config.settings import (
    STATUS_FILE, LOG_FILE, DUPLICATE_HASHES_FILE,
    RENAMING_INDEX_FILE, DEFAULT_BATCH_SIZE, DEFAULT_BATCH_INTERVAL
)


class PhotoOrganizer:
    """照片/视频整理器"""

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

        # 文件命名器
        self.renaming_index: Dict[str, int] = {}
        self.file_namer = FileNamer(self.renaming_index)

        # 设置日志
        self._setup_logging()

        # 设置信号处理
        self._setup_signal_handlers()

        # 注册退出处理
        atexit.register(self._on_exit)

        # 文件处理器
        self.processor = FileProcessor(
            status_data=self.status_data,
            duplicate_hashes=self.duplicate_hashes,
            file_namer=self.file_namer,
            dry_run=dry_run
        )

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

        stats = self.processor.get_stats()
        self.logger.info(f"程序退出 - 成功: {stats.success}, "
                        f"失败: {stats.failed}, "
                        f"跳过重复: {stats.skipped_duplicate}")

    def _check_directory_health(self, check_dir: Path, dir_name: str = "目录") -> Dict[str, any]:
        """
        检查指定目录的健康度

        Args:
            check_dir: 要检查的目录
            dir_name: 目录名称（用于日志）

        Returns:
            健康度检查结果
        """
        import shutil

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
            health_status['target']['free_space_gb'] > 5
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

    @staticmethod
    def _parse_date_from_filename(file_path: Path):
        """
        从文件名解析日期（静态方法）

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

    def print_scan_summary(self, media_files: List[Path]):
        """
        打印扫描结果摘要

        Args:
            media_files: 扫描到的媒体文件列表
        """
        total_files = len(media_files)
        total_size = 0
        format_counts = {}
        year_month_counts = {}

        self.logger.info("分析文件信息...")

        for file_path in media_files:
            try:
                file_size = file_path.stat().st_size
                total_size += file_size

                ext = file_path.suffix.lower()
                format_counts[ext] = format_counts.get(ext, 0) + 1

                # 尝试获取日期进行统计
                photo_date = PhotoOrganizer._parse_date_from_filename(file_path)
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
        for ext_format, count in sorted(format_counts.items(), key=lambda x: -x[1])[:10]:
            self.logger.info(f"  {ext_format.ljust(8)}: {count:,} ({count/total_files*100:.1f}%)")

        self.logger.info(f"\n时间分布 (前10个):")
        for ym, count in sorted(year_month_counts.items())[:10]:
            self.logger.info(f"  {ym}: {count:,}")

        self.logger.info(f"\n批次处理配置:")
        batches = (total_files + self.batch_size - 1) // self.batch_size
        self.logger.info(f"  批次大小: {self.batch_size}")
        self.logger.info(f"  批次间间隔: {self.batch_interval}秒")
        self.logger.info(f"  总批次数: {batches}")
        estimated_time = batches * self.batch_interval
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
        index_path = self.target_dir / 'logs' / RENAMING_INDEX_FILE

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
                                self.status_data[key] = record
                            else:
                                self.logger.warning(
                                    f"检测到不一致记录: {source_path} 状态为成功但文件仍存在，将重新处理"
                                )
                                removed_count += 1
                        elif record.status == FileProcessingStatus.FAILED.value:
                            if not source_path.exists():
                                self.logger.debug(f"清理无效失败记录: {source_path} 不存在")
                                removed_count += 1
                            else:
                                self.status_data[key] = record
                        else:
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
            from dataclasses import asdict
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

        # 第二步：扫描媒体文件
        self.logger.info("\n【第二步】扫描媒体文件")
        try:
            scanner = FileScanner(self.source_dir, lambda: self.stop_requested)
            media_files = scanner.scan_media_files()
        except Exception as e:
            self.logger.error(f"查找媒体文件失败: {e}")
            return

        stats = self.processor.get_stats()
        stats.total = len(media_files)

        if stats.total == 0:
            self.logger.warning("没有找到媒体文件")
            return

        # 第三步：显示扫描摘要并等待确认
        self.logger.info("\n【第三步】分析并确认处理计划")
        self.print_scan_summary(media_files)

        if not self.wait_for_confirmation():
            self.logger.info("用户取消操作")
            return

        # 第四步：加载之前的状态
        self.logger.info("\n【第四步】准备处理")
        if self._load_status():
            self.logger.info("检测到之前的状态，将尝试继续...")

        # 过滤已处理的文件
        remaining_files = []
        for file_path in media_files:
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
            if batch_num > 0:
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
                if self.processor.process_file(file_path, self._get_target_directory):
                    batch_success += 1
                else:
                    batch_failed += 1

            # 批次处理完成
            batch_elapsed = time.time() - batch_start_time
            self.logger.info(f"批次完成 - 耗时: {batch_elapsed:.1f}秒, "
                           f"成功: {batch_success}, 失败: {batch_failed}")

            # 定期保存状态
            current_time = time.time()
            if current_time - last_save_time > 60:
                self._save_status()
                last_save_time = current_time

            # 批次间等待（最后一批不需要等待）
            if batch_num < total_batches - 1 and not self.stop_requested:
                self.logger.info(f"等待 {self.batch_interval} 秒后处理下一批次...")
                time.sleep(self.batch_interval)

        # 最终保存状态
        self._save_status()

        # 显示最终统计
        stats = self.processor.get_stats()
        elapsed = time.time() - start_time
        self.logger.info("\n" + "=" * 60)
        self.logger.info("照片整理完成")
        self.logger.info(f"总用时: {elapsed/60:.1f} 分钟")
        self.logger.info(f"总文件数: {stats.total}")
        self.logger.info(f"成功: {stats.success}")
        self.logger.info(f"失败: {stats.failed}")
        self.logger.info(f"跳过重复: {stats.skipped_duplicate}")
        self.logger.info(f"Live Photos 配对: {stats.live_photo_pairs}")
        self.logger.info(f"视频文件: {stats.video_files}")
        self.logger.info(f"平均速度: {stats.total/elapsed:.1f} 个/秒")
        self.logger.info("=" * 60)
