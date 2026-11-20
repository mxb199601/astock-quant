#!/bin/bash
# 把「量化研究」装到当前用户的应用程序文件夹。打开它就会拉起页面服务。
set -eu

ROOT="/Users/meng/测试/量化工具"
APP="$HOME/Applications/量化研究.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"
ICONSET="$RES/AppIcon.iconset"

chmod 755 "$ROOT/scripts/quant_app.sh"
SRC="$(mktemp -t quant-research).applescript"
cat >"$SRC" <<EOF
property scriptPath : "$ROOT/scripts/quant_app.sh"

on startServer()
  try
    do shell script quoted form of scriptPath & " ensure"
  on error errMsg
    display alert "量化研究没起来" message errMsg as warning
    error number -128
  end try
end startServer

on run
  my startServer()
  open location "http://127.0.0.1:8765/"
end run

on reopen
  my startServer()
  open location "http://127.0.0.1:8765/"
end reopen

-- 程序坞里还开着时，服务可能被别处结束；这里定时补起，不弹框。
on idle
  try
    do shell script quoted form of scriptPath & " ensure"
  end try
  return 10
end idle

on quit
  try
    do shell script quoted form of scriptPath & " stop"
  end try
  continue quit
end quit
EOF

rm -rf "$APP"
osacompile -s -o "$APP" "$SRC"
rm -f "$SRC"
mkdir -p "$ICONSET"
PLIST="$APP/Contents/Info.plist"
set_plist() {
  /usr/libexec/PlistBuddy -c "Delete :$1" "$PLIST" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
set_plist CFBundleDisplayName "量化研究"
set_plist CFBundleName "量化研究"
set_plist CFBundleIdentifier "com.meng.quant-research"

python3 - "$ICONSET/icon_1024.png" <<'PY'
import struct, sys, zlib

path, = sys.argv[1:]
w = h = 1024

def pixel(x, y):
    nx = (x + 0.5) / w
    ny = (y + 0.5) / h
    m, rad = 0.04, 0.22
    ix = (nx - m) / (1 - 2 * m)
    iy = (ny - m) / (1 - 2 * m)
    if not (0 <= ix <= 1 and 0 <= iy <= 1):
        return (0, 0, 0, 0)
    dx = min(ix, 1 - ix)
    dy = min(iy, 1 - iy)
    if dx < rad and dy < rad:
        if (rad - dx) ** 2 + (rad - dy) ** 2 > rad * rad:
            return (0, 0, 0, 0)
    bg = (16, 36, 30, 255)

    def candle(cx, top, bot, color, wick=0.012):
        half = 0.055
        if abs(nx - cx) <= wick and top - 0.06 <= ny <= bot + 0.06:
            return color
        if abs(nx - cx) <= half and top <= ny <= bot:
            return color
        return None

    for cx, top, bot, color in (
        (0.34, 0.46, 0.62, (225, 90, 74, 255)),
        (0.50, 0.22, 0.74, (61, 196, 122, 255)),
        (0.66, 0.36, 0.58, (61, 196, 122, 255)),
    ):
        hit = candle(cx, top, bot, color)
        if hit:
            return hit
    return bg

raw = bytearray()
for y in range(h):
    raw.append(0)
    for x in range(w):
        raw.extend(pixel(x, y))

def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
open(path, "wb").write(png)
PY

for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICONSET/icon_1024.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  dbl=$((size * 2))
  cp "$ICONSET/icon_${size}x${size}.png" "$ICONSET/icon_${size}x${size}@2x.png" 2>/dev/null || true
  sips -z "$dbl" "$dbl" "$ICONSET/icon_1024.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"
cp "$RES/AppIcon.icns" "$RES/applet.icns"
rm -rf "$ICONSET"

codesign --force --deep --sign - "$APP" >/dev/null
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREG" ]; then
  "$LSREG" -f "$APP" >/dev/null
fi
echo "已安装 $APP"
