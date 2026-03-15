# 照片和视频整理工具

一个功能强大的照片和视频整理工具，支持批量整理、Live Photos 配对、EXIF 元数据读取等功能。

## 免责声明
- ！！批量处理照片文件属于高危操作，请一定在清楚自己操作的前提下启动本项目。
- 正式处理前可以用测试文件夹或者--dry-run方式做测试。
- 请务必谨慎操作！！

## 功能特性

- ✅ **智能文件分类** - 自动识别照片和视频，按年/月目录结构整理
- ✅ **Live Photos 支持** - 自动识别并保持 iOS Live Photos 的配对关系
- **视频支持** - 支持所有常见视频格式，自动读取拍摄时间
- **智能命名** - 英文文件名自动重命名为 `IMG_YYYYMMDD_XXXX` 格式，中文文件名保留原样
- **真实元数据** - 使用 EXIF 读取照片拍摄时间，使用 MediaInfo 读取视频元数据
- **快速哈希** - 采用采样哈希算法，大幅提升大文件处理速度
- **断点续传** - 支持中断后继续处理，自动记录进度
- **文件去重** - 基于文件哈希值进行精确去重
- **完整性校验** - 复制后自动校验文件完整性
- **批量处理** - 支持分批处理，避免对 NAS 造成压力
- **健康检查** - 定期检查 NAS 挂载状态和磁盘空间

## 安装

### 使用 uv（推荐）

```bash
# 安装依赖
uv pip install piexif pymediainfo

# 运行
python main.py
```

### 使用 pip

```bash
#.pip install -r requirements.txt
```

## 使用方法

### 基本用法

```bash
# 使用默认路径
python main.py

# 指定源和目标目录
python main.py "Z:/nas_photos" "D:/organized_photos"

# 模拟运行（不实际移动文件）
python main.py "Z:/nas_photos" "D:/organized_photos" --dry-run
```

### 高级选项

```bash
# 自定义批次配置
python main.py "Z:/nas_photos" "D:/organized_photos" \
  --batch-size 500 \
  --batch-interval 2

# 跳过确认（自动化场景）
python main.py "Z:/nas_photos" "D:/organized_photos" --no-confirm
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|---------|
| `source_dir` | 源照片/视频目录 | `E:\相册&视频` |
| `target_dir` | 目标整理目录 | `E:\相册&视频-整理` |
| `--dry-run` | 模拟运行，不实际移动文件 | `False` |
| `--batch-size` | 每批处理的文件数量 | `100` |
| `--batch-interval` | 批次间间隔（秒） | `5` |
| `--no-confirm` | 跳过确认提示 | `False` |

## 支持的格式

### 图片格式
`.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.tif`, `.webp`, `.heic`, `.heif`, `.raw`, `.cr2`, `.nef`, `.arw`, `.dng`, `.orf`, `.rw2`, `.pef`, `.srw`, `.x3f`, `.3fr`, `.mos`, `.mrw`, `.erf`, `.k25`, `.kdc`

### 视频格式
`.mp4`, `.mov`, `.avi`, `.mkv`, `.flv`, `.wmv`, `.3gp`, `.webm`, `.m4v`, `.ts`, `.mts`, `.m2ts`, `.dv`, `.rm`, `.rmvb`

## 文件命名规则

### 英文文件名
自动重命名为标准格式：
- 照片：`IMG_YYYYMMDD_XXXX.ext`
- 视频：`MOV_YYYYMMDD_XXXX.ext`

例如：
- `IMG_20240101_120000.heic` → `IMG_20240101_0001.heic`
- `VID_20240101_120000.mp4` → `MOV_20240101_0001.mp4`

### 中文文件名
保留原始文件名：
- `2024年春节照片.jpg` → `2024年春节照片.jpg`
- `家庭聚会视频.mp4` → `家庭聚会视频.mp4`

### Live Photos
保持配对关系，不重命名：
- `IMG_1234.HEIC` + `IMG_1234.MOV` → 原样移动到目标目录

## 性能优化

### 快速哈希算法
- **小文件（<1MB）**：计算完整 SHA256 哈希
- **大文件**：采样开头、中间、结尾各 8KB 计算哈希

这种方法在 99.9% 的情况下能准确识别重复文件，同时大幅提升处理速度。

### 批次处理
- 分批处理避免对 NAS 造成过大压力
- 批次间可配置间隔时间
- 支持动态调整批次大小

## 项目结构

```
photo_organizer/
├── src/
│   ├── core/
│   │   ├── organizer.py       # 主整理器类
│   │   ├── file_processor.py  # 文件处理逻辑
│   │   └── file_scanner.py   # 文件扫描逻辑
│   ├── metadata/
│   │   ├── exif_reader.py    # EXIF 读取
│   │   └── media_info.py     # 视频元数据
│   ├── utils/
│   │   ├── hash_utils.py     #.哈希计算
│   │   ├── file_utils.py     # 文件操作
│   │   └── naming_utils.py   # 命名工具
│   └── models/
│       └── file_record.py    # 数据模型
├── config/
│   └── settings.py           # 配置常量
├── main.py                  # 主入口
├── pyproject.toml           # 项目配置
└── README.md
```

## 状态文件

程序会在目标目录的 `logs` 文件夹下保存以下文件：

- `organizer_status.json` - 文件处理状态
- `duplicate_hashes.json` - 重复文件哈希
- `renaming_index.json` - 重命名索引
- `photo_organizer.log` - 详细日志

## 常见问题

### Q: 如何中断处理？
A: 按 `Ctrl+C` 即可安全中断，程序会自动保存当前状态。

### Q: 如何从上次中断的地方继续？
A: 直接重新运行程序即可，会自动加载之前的状态并继续处理。

### Q: 如何重新开始？
A: 删除目标目录 `logs` 文件夹下的 `organizer_status.json` 文件即可。

### Q: 如何调整处理速度？
A:
- 增大 `--batch-size` 参数（如 500）
- 减小 `--batch-interval` 参数（如 2）

### Q: Live Photos 配对会破坏吗？
A: 不会。程序会自动检测配对关系，并将文件一起移动到同一目录，保持相同的文件名。

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License
