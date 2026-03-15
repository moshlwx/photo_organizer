"""
文件工具函数
"""
import os
import shutil
import logging
from pathlib import Path
from typing import Optional


def ensure_directory(path: Path) -> None:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)


def verify_file_integrity(source: Path, target: Path,
                        calculate_hash_func,
                        threshold: int = 10 * 1024 * 1024) -> bool:
    """
    校验文件复制后的完整性

    Args:
        source: 源文件路径
        target: 目标文件路径
        calculate_hash_func: 哈希计算函数
        threshold: 大文件阈值，大于此大小做快速校验

    Returns:
        完整性校验是否通过
    """
    logger = logging.getLogger('FileUtils')

    try:
        # 检查文件大小
        source_size = source.stat().st_size
        target_size = target.stat().st_size

        if source_size != target_size:
            logger.error(f"文件大小不匹配: {source_size} vs {target_size}")
            return False

        # 大文件做快速哈希校验
        if source_size >= threshold:
            source_hash = calculate_hash_func(source)
            target_hash = calculate_hash_func(target)

            if source_hash != target_hash:
                logger.error("文件哈希不匹配")
                return False

        return True

    except Exception as e:
        logger.error(f"完整性校验异常: {e}")
        return False


def move_file_safe(source: Path, target: Path,
                 verify_func, dry_run: bool = False,
                 logger: Optional[logging.Logger] = None) -> bool:
    """
    安全移动文件（带完整性校验）

    流程：
    1. 先复制文件
    2. 校验复制后的文件完整性
    3. 仅校验通过后删除源文件

    Args:
        source: 源文件路径
        target: 目标文件路径
        verify_func: 完整性校验函数
        dry_run: 是否为模拟运行
        logger: 日志记录器

    Returns:
        是否成功
    """
    if logger is None:
        logger = logging.getLogger('FileUtils')

    if dry_run:
        logger.info(f"[模拟] 将 {source} 移动到 {target}")
        return True

    try:
        # 确保目标目录存在
        target.parent.mkdir(parents=True, exist_ok=True)

        # 步骤1：先复制文件（保留元数据）
        shutil.copy2(str(source), str(target))

        # 步骤2：校验复制后的文件完整性
        if verify_func(source, target):
            # 步骤3：仅校验通过后删除源文件
            source.unlink()
            return True
        else:
            # 校验失败，删除不完整的目标文件
            if target.exists():
                target.unlink(missing_ok=True)
            logger.error(f"文件复制后完整性校验失败: {source}")
            return False

    except Exception as e:
        # 异常时清理目标文件（避免残留不完整文件）
        if target.exists():
            target.unlink(missing_ok=True)
        logger.error(f"移动文件失败 {source} -> {target}: {e}")
        return False


def get_unique_filename(target_dir: Path, original_filename: str) -> str:
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
