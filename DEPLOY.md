# Deploy Guide - Claude Token Tracker

## Cấu trúc dự án

```
claude-token-tracker/
├── app.py                  # Entry point chính (+ --install/--uninstall trên Linux)
├── track_tokens.py         # Đọc transcript, gộp token + chi phí theo ngày, model, project
├── sources.py              # Tìm thư mục transcript: ~/.claude/projects, ~/.config/claude/projects, hoặc CLAUDE_CONFIG_DIR
├── pricing.py              # Bảng giá + tính chi phí (API-equivalent)
├── dashboard.py            # Sinh HTML dashboard với Chart.js + flatpickr
├── test_pricing.py         # Test cho pricing.py (unittest)
├── test_track_tokens.py    # Test đọc transcript, khử trùng lặp, ngày địa phương, tên project
├── test_dashboard.py       # Test trang dashboard (helper JS chạy bằng node nếu máy có)
├── test_app.py             # Test cửa sổ app (giả lập pywebview)
├── build.py                # Script build cho từng OS
├── gen_icon.py             # Tạo icon (chỉ chạy trên macOS)
├── assets/
│   ├── chart.min.js        # Chart.js v4 (inline vào HTML)
│   ├── flatpickr.min.js    # Flatpickr - date range picker
│   ├── flatpickr.min.css   # Flatpickr styles
│   ├── flatpickr-dark.css  # Flatpickr dark theme
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

Ubuntu 23.04+ / Debian 12+ và Python của Homebrew từ chối `pip install` toàn hệ thống (PEP 668,
`externally-managed-environment`): tạo venv trước, như ở mục Linux bên dưới.

### Chạy test trước khi build

```bash
python -m unittest
```

Test chỉ dùng thư viện chuẩn. Máy có `node` thì chạy thêm test cho helper JS của dashboard; không có thì các test đó tự bỏ qua.

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

Bản Linux dùng backend **Qt/QtWebEngine** (tự chứa Chromium) cho cửa sổ native — không phụ thuộc GTK/WebKit của hệ thống, nên tránh được lỗi xung đột thư viện trên Ubuntu 24.04.

```bash
# Cài thư viện runtime cho QtWebEngine (Chromium). libxcb-cursor0: thiếu nó, Qt không mở được cửa sổ trên X11
# (app sẽ mở dashboard bằng trình duyệt và in lệnh apt này).
# Ubuntu 24.04 đổi tên libasound2 thành libasound2t64 (gõ libasound2 thì apt báo "has no installation candidate");
# Ubuntu 22.04 / Debian 12: thay libasound2t64 bằng libasound2.
sudo apt-get install -y \
  libnss3 libnspr4 libxcomposite1 libxdamage1 libxrandr2 \
  libxkbcommon0 libasound2t64 libgbm1 libxtst6 libxshmfence1 libxcb-cursor0 python3-venv

# Python deps cho Linux (thêm qtpy + PySide6, khác macOS/Windows), trong venv vì PEP 668
python3 -m venv ~/.venvs/ctt-build
source ~/.venvs/ctt-build/bin/activate
pip install pyinstaller pywebview qtpy PySide6

# Cài appimagetool để tạo .AppImage
wget -q "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
  -O ~/.local/bin/appimagetool
chmod +x ~/.local/bin/appimagetool

# Build (Linux build ở dạng onedir rồi đóng gói thành AppImage)
python build.py

# Output:
#   dist/claude-token-tracker-x86_64.AppImage
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
- **Linux .AppImage**: cần `chmod +x` trước khi chạy. Ubuntu 24.04 không cài sẵn FUSE — nếu chạy trực tiếp không mở, dùng `./claude-token-tracker-x86_64.AppImage --appimage-extract-and-run` hoặc `sudo apt install libfuse2t64`.
- **Đưa app vào menu (Linux)**: chạy `./claude-token-tracker-x86_64.AppImage --appimage-extract-and-run --install` để giải nén app vào `~/.local/lib/` và thêm vào menu ứng dụng — mở từ menu **không cần FUSE**. Gỡ bằng `--uninstall`.
- **Kích thước AppImage**: bản Linux kèm Chromium (QtWebEngine) nên nặng hơn (~245 MB, đo ngày 13-10-2026).
- **File `token_usage.json` của bản đóng gói**: không nằm cạnh app (mount AppImage chỉ đọc, `.app` có chữ ký) mà ở
  thư mục dữ liệu của user: `~/Library/Application Support/claude-token-tracker/` (macOS),
  `%LOCALAPPDATA%\claude-token-tracker\` (Windows), `~/.local/share/claude-token-tracker/` (Linux).
- **Icon**: đã có sẵn cho cả 3 OS trong `assets/`. Nếu cần đổi icon, sửa `gen_icon.py` và chạy lại (chỉ chạy được trên macOS).
