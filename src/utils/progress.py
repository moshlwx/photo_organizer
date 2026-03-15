"""
进度条显示模块
"""
import sys
import time
from typing import Optional


class ProgressBar:
    """美观的进度条"""

    def __init__(self, total: int, description: str = "处理中"):
        """
        初始化进度条

        Args:
            total: 总项目数
            description: 进度条描述
        """
        self.total = total
        self.current = 0
        self.description = description
        self.start_time = time.time()
        self.last_update_time = 0
        self.last_update_count = 0
        self.width = 60  # 进度条宽度
        self.errors = 0
        self.live_photo_pairs = 0

        # 速度计算
        self.speed_samples = []
        self.max_speed_samples = 10

    def update(self, n: int = 1, error: bool = False,
               live_photo: bool = False):
        """
        更新进度

        Args:
            n: 增加的数量（默认1）
            error: 是否发生错误
            live_photo: 是否是 Live Photos 配对
        """
        self.current += n

        if error:
            self.errors += 1

        if live_photo:
            self.live_photo_pairs += 1

        # 限制更新频率（每0.1秒或每10个项目更新一次）
        now = time.time()
        if now - self.last_update_time < 0.1 and \
           self.current - self.last_update_count < 10:
            return

        self.last_update_time = now
        self.last_update_count = self.current
        self._display()

    def _display(self):
        """显示进度条"""
        # 计算进度
        progress = self.current / self.total if self.total > 0 else 1
        percentage = progress * 100

        # 计算速度
        elapsed = time.time() - self.start_time
        speed = self.current / elapsed if elapsed > 0 else 0

        # 维护速度样本（用于更稳定的显示）
        self.speed_samples.append(speed)
        if len(self.speed_samples) > self.max_speed_samples:
            self.speed_samples.pop(0)
        avg_speed = sum(self.speed_samples) / len(self.speed_samples)

        # 预估剩余时间
        if avg_speed > 0:
            remaining = (self.total - self.current) / avg_speed
        else:
            remaining = 0

        # 构建进度条
        filled = int(self.width * progress)
        bar = '█' * filled + '░' * (self.width - filled)

        # 格式化时间
        elapsed_str = self._format_time(elapsed)
        remaining_str = self._format_time(remaining)

        # 构建输出
        # 使用 \r 回到行首，实现原地更新
        output = f"\r{self.description}: [{bar}] {self.current:,}/{self.total:,} ({percentage:.1f}%) "
        output += f"速度: {avg_speed:.1f}/秒 | 已用: {elapsed_str} | 剩余: {remaining_str}"

        # 添加额外信息
        extras = []
        if self.live_photo_pairs > 0:
            extras.append(f"Live: {self.live_photo_pairs}")
        if self.errors > 0:
            extras.append(f"错误: {self.errors}")

        if extras:
            output += f" | {' | '.join(extras)}"

        # 输出（不换行）
        sys.stdout.write(output)
        sys.stdout.flush()

    def _format_time(self, seconds: float) -> str:
        """格式化时间为可读字符串"""
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds %)60
            return f"{minutes}分{secs}秒"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}小时{minutes}分"

    def close(self):
        """完成进度条，输出换行"""
        self._display()
        sys.stdout.write('\n')
        sys.stdout.flush()

        # 显示摘要
        elapsed = time.time() - self.start_time
        elapsed_str = self._format_time(elapsed)

        if self.errors > 0:
            print(f"\n✓ 完成！共处理 {self.current:,} 个文件，用时 {elapsed_str}，遇到 {self.errors} 个错误")
        else:
            print(f"\n✓ 完成！共处理 {self.current:,} 个文件，用时 {elapsed_str}")

        if self.live_photo_pairs > 0:
            print(f"ℹ 识别到 {self.live_photo_pairs} 个 Live Photos 配对")


class BatchProgress:
    """批次进度显示"""

    def __init__(self, total_batches: int, total_files: int):
        """
        初始化批次进度

        Args:
            total_batches: 总批次数
            total_files: 总文件数
        """
        self.total_batches = total_batches
        self.current_batch = 0
        self.total_files = total_files
        self.processed_files = 0
        self.start_time = time.time()
        self.batch_start_time = time.time()
        self.batch_files = 0

    def start_batch(self, batch_num: int, batch_size: int,
                  start_index: int, end_index: int):
        """
        开始新批次

        Args:
            batch_num: 批次号
            batch_size: 批次大小
            start_index: 起始索引
            end_index: 结束索引
        """
        self.current_batch = batch_num
        self.batch_start_time = time.time()
        self.batch_files = batch_size

        # 计算累积进度
        prev_files = self.processed_files
        self.processed_files = min(end_index, self.total_files)

        # 显示批次信息
        print(f"\n{'=' * 60}")
        print(f"📦 批次 {batch_num}/{self.total_batches}")
        print(f"   文件: {start_index + 1:,}-{min(end_index, self.total_files):,} (共 {batch_size} 个)")
        print(f"{'=' * 60}")

    def update_batch(self, n: int = 1, error: bool = False,
                   live_photo: bool = False):
        """
        更新批次内进度

        Args:
            n: 增加的数量
            error: 是否发生错误
            live_photo: 是否是 Live Photos
        """
        self.batch_files += n

        # 每10个文件显示一次
        if self.batch_files % 10 == 0 or self.batch_files == 1:
            elapsed = time.time() - self.batch_start_time
            speed = self.batch_files / elapsed if elapsed > 0 else 0

            print(f"  进度: {self.batch_files} | 速度: {speed:.1f}/秒")

    def end_batch(self, success: int, failed: int):
        """
        结束批次

        Args:
            success: 成功数
            failed: 失败数
        """
        elapsed = time.time() - self.batch_start_time
        speed = self.batch_files / elapsed if elapsed > 0 else 0

        print(f"\n  ✓ 批次完成 - 耗时: {elapsed:.1f}秒, "
              f"成功: {success}, 失败: {failed}, 速度: {speed:.1f}/秒")

        # 显示整体进度
        overall_elapsed = time.time() - self.start_time
        overall_speed = self.processed_files / overall_elapsed if overall_elapsed > 0 else 0
        progress_pct = (self.processed_files / self.total_files * 100) if self.total_files > 0 else 0

        print(f"\n  📊 整体进度: {progress_pct:.1f}% ({self.processed_files:,}/{self.total_files:,}) "
              f"| 总速度: {overall_speed:.1f}/秒")

        # 预估剩余时间
        if overall_speed > 0:
            remaining = (self.total_files - self.processed_files) / overall_speed
            from .progress import ProgressBar  # 避免循环导入
            remaining_str = ProgressBar._format_time_static(remaining)
            print(f"  ⏱ 预计剩余时间: {remaining_str}")

    @staticmethod
    def _format_time_static(seconds: float) -> str:
        """静态方法：格式化时间"""
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{minutes}分{secs}秒"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}小时{minutes}分"
