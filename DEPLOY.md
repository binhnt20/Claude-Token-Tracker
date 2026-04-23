# Deploy Guide - Claude Token Tracker

## Cấu trúc dự án

```
claude-token-tracker/
├── app.py                  # Entry point chính
├── track_tokens.py         # Thu thập dữ liệu từ ~/.claude/projects/
├── dashboard.py            # Sinh HTML dashboard với Chart.js
├── build.py                # Script build cho từng OS
├── gen_icon.py             # Tạo icon (chỉ chạy trên macOS)
├── assets/
│   ├── chart.min.js        # Chart.js v4 (inline vào HTML)
│   ├── icon.png            # Icon gốc 512x512
│   ├── icon.icns           # Icon cho macOS
│   ├── icon.ico            # Icon cho Windows
│   └── icon-256.png        # Icon cho Linux
├── .github/workflows/
│   └── build.yml           # GitHub Actions CI/CD
├── .gitignore
├── README.md
└── DEPLOY.md               # File này
```

## Build trên máy local

### Yêu cầu

```bash
pip install pyinstaller pywebview
```

### macOS

```bash
# Cài thêm create-dmg để tạo file .dmg
brew install create-dmg

# Build
python build.py

# Output:
#   dist/claude-token-tracker.app   (kéo vào Applications)
#   dist/claude-token-tracker.dmg   (chia sẻ cho người khác)
```

### Windows

```bash
# Build (chạy trên máy Windows)
python build.py

# Output:
#   dist/claude-token-tracker.exe
```

### Linux (Ubuntu/Debian)

```bash
# Cài system dependencies cho pywebview
sudo apt-get install -y \
  libgirepository1.0-dev libcairo2-dev \
  libwebkit2gtk-4.1-dev gir1.2-webkit2-4.1

# Cài appimagetool để tạo .AppImage
wget -q "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
  -O ~/.local/bin/appimagetool
chmod +x ~/.local/bin/appimagetool

# Build
python build.py

# Output:
#   dist/claude-token-tracker-x86_64.AppImage
#   (hoặc dist/claude-token-tracker nếu không có appimagetool)
```

## Build tự động với GitHub Actions

GitHub Actions build cho cả 3 OS cùng lúc. Có 2 cách trigger:

### Cách 1: Tạo tag release

```bash
# Lần đầu: init repo và push code
cd claude-token-tracker
git init
git add .
git commit -m "Claude Token Tracker v1.0.0"
git remote add origin https://github.com/<username>/claude-token-tracker.git
git push -u origin main

# Tạo tag để trigger build
git tag v1.0.0
git push --tags
```

Khi push tag `v*`, GitHub Actions sẽ:

1. Build `.dmg` trên macOS runner
2. Build `.exe` trên Windows runner
3. Build `.AppImage` trên Ubuntu runner
4. Upload tất cả vào **GitHub Releases** tự động

### Cách 2: Trigger thủ công

1. Vào repo trên GitHub
2. Tab **Actions** → workflow **Build Cross-Platform**
3. Bấm **Run workflow** → **Run workflow**
4. Khi hoàn thành, download artifacts từ trang workflow run

### Cách 3: Push code mới và tạo release

```bash
# Sửa code...
git add .
git commit -m "Fix: bar chart width"

# Tạo tag mới
git tag v1.0.1
git push origin main --tags
```

## Kiểm tra build

Sau khi CI chạy xong:

1. Vào **Actions** tab → chọn workflow run mới nhất
2. Nếu thành công, artifacts xuất hiện ở cuối trang:
   - `macos-dmg` → `claude-token-tracker.dmg`
   - `windows-exe` → `claude-token-tracker.exe`
   - `linux-appimage` → `claude-token-tracker-x86_64.AppImage`
3. Nếu có tag `v*`, artifacts cũng được upload vào **Releases**

## Lưu ý

- **PyInstaller không hỗ trợ cross-compile**: phải build trên chính OS đích, hoặc dùng GitHub Actions.
- **macOS .app**: có thể bị Gatekeeper chặn khi download. User cần chuột phải → Open lần đầu.
- **Linux .AppImage**: cần `chmod +x` trước khi chạy.
- **Icon**: đã có sẵn cho cả 3 OS trong `assets/`. Nếu cần đổi icon, sửa `gen_icon.py` và chạy lại (chỉ chạy được trên macOS).
