#!/usr/bin/env python3
"""
主入口 - 照片/视频整理工具
"""
import sys
from pathlib import Path

# 添加 src 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from src.core.organizer import PhotoOrganizer
from config.settings import DEFAULT_BATCH_SIZE, DEFAULT_BATCH_INTERVAL


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description='照片/视频整理工具 - 将照片和视频按年/月目录结构整理',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 整理照片和视频
  python main.py Z:/nas_photos D:/organized_photos

  # 模拟运行（不实际移动文件）
  python main.py Z:/nas_photos D:/organized_photos --dry-run

  # 自定义批次配置
  python main.py Z:/nas_photos D:/organized_photos --batch-size 50 --batch-interval 10

  # 跳过确认（自动化场景）
  python main.py Z:/nas_photos D:/organized_photos --no-confirm
        """
    )

    parser.add_argument(
        'source_dir',
        default='E:\相册&视频',
        help='源照片/视频目录路径'
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
            organizer.wait_for_confirmation = lambda: True

        organizer.organize()
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
