"""
配置文件 - 所有常量和配置项
"""

# 支持的图片格式
SUPPORTED_IMAGE_FORMATS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tiftif',
    '.webp', '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw',
    '.dng', '.orf', '.rw2', '.pef', '.srw', '.x3f', '.3fr',
    '.mos', '.mrw', '.erf', '.k25', '.kdc'
}

# 支持的视频格式
SUPPORTED_VIDEO_FORMATS = {
    '.mp4', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.3gp',
    '.webm', '.m4v', '.ts', '.mts', '.m2ts', '.dv', '.rm', '.rmvb'
}

# Live Photos 的视频格式
LIVE_PHOTO_VIDEO_FORMATS = {'.mov'}

# 所有支持的媒体格式
SUPPORTED_MEDIA_FORMATS = SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS

# 状态文件名
STATUS_FILE = 'organizer_status.json'
LOG_FILE = 'photo_organizer.log'
DUPLICATE_HASHES_FILE = 'duplicate_hashes.json'
RENAMING_INDEX_FILE = 'renaming_index.json'
SCAN_RESULTS_FILE = 'scan_results.json'

# 默认批次配置
DEFAULT_BATCH_SIZE = 100
DEFAULT_BATCH_INTERVAL = 5  # 秒
MAX_RETRIES = 3

# 哈希计算配置
FAST_HASH_SIZE = 8192  # 采样块大小
FAST_HASH_THRESHOLD = 1024 * 1024  # 1MB，小于此大小用完整哈希
INTEGRITY_CHECK_THRESHOLD = 10 * 1024 * 1024  # 10MB，大于此大小做快速校验
