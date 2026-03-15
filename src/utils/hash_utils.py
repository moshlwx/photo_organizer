"""
哈希计算工具
"""
import hashlib
from pathlib import Path
from typing import Optional

# 从配置导入常量
from config.settings import FAST_HASH_SIZE, FAST_HASH_THRESHOLD


def calculate_file_hash(file_path: Path) -> Optional[str]:
    """
    计算文件哈希值（使用快速采样法）

    对于小文件（<1MB）计算完整哈希
    对于大文件，只采样文件的开头、中间、结尾部分进行哈希

    Args:
        file_path: 文件路径

    Returns:
        文件的SHA256哈希值（快速采样版）
    """
    try:
        file_size = file_path.stat().st_size
        sha256_hash = hashlib.sha256()

        # 小文件（<1MB）：计算完整哈希
        if file_size < FAST_HASH_THRESHOLD:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(FAST_HASH_SIZE), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()

        # 大文件：快速采样哈希
        with open(file_path, "rb") as f:
            # 1. 读取开头部分
            f.seek(0)
            start_chunk = f.read(FAST_HASH_SIZE)
            sha256_hash.update(start_chunk)

            # 2. 读取中间部分
            f.seek(file_size // 2)
            middle_chunk = f.read(FAST_HASH_SIZE)
            sha256_hash.update(middle_chunk)

            # 3. 读取结尾部分
            f.seek(max(0, file_size - FAST_HASH_SIZE))
            end_chunk = f.read(FAST_HASH_SIZE)
            sha256_hash.update(end_chunk)

            # 4. 将文件大小也加入哈希（避免不同大小的文件得到相同采样）
            sha256_hash.update(str(file_size).encode('utf-8'))

        return sha256_hash.hexdigest()

    except Exception as e:
        return None
