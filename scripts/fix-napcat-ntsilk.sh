#!/bin/sh
# 让 NapCat 的 ffmpeg 支持腾讯 SILK（ntsilk_s16le），从而恢复语音消息发送。
#
# 背景：
#   NapCat 4.18.36+ 在部分平台（常见于 ARM64 Docker）会退回系统 ffmpeg，
#   而系统 ffmpeg 没有 ntsilk 封装器，发送语音时报：
#     Requested output format 'ntsilk_s16le' is not a suitable output format
#
# 用法（在 NapCat 容器内以 root 执行）：
#   sh scripts/fix-napcat-ntsilk.sh          # 安装 / 修复
#   sh scripts/fix-napcat-ntsilk.sh --check  # 仅检查（0=已就绪）
#
# 说明：
#   - 脚本会保留系统 ffmpeg/ffprobe 原文件为 *.real，并写入一个很小的分发脚本。
#   - 只把带 ntsilk 参数的 ffmpeg 调用交给 ntsilk 二进制，其他调用仍走原 ffmpeg。
#   - ffprobe 增加 silk 文件的时长兜底（按文件大小估算），避免 NapCat 读取
#     silk 时长时失败。
#   - 操作可重复执行；已修复时会直接退出。
set -eu

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

FFMPEG_BIN=/usr/bin/ffmpeg
FFPROBE_BIN=/usr/bin/ffprobe
NTSILK_BIN=/usr/local/bin/ffmpeg-ntsilk
MARK='# nekro-plugin-mimo-tts-ntsilk-wrapper'

say() {
    printf '[mimo-tts] %s\n' "$*"
}

is_ready() {
    [ -x "$NTSILK_BIN" ] || return 1
    [ -f "$FFMPEG_BIN" ] || return 1
    grep -q "$MARK" "$FFMPEG_BIN" 2>/dev/null || return 1
    grep -q "$MARK" "$FFPROBE_BIN" 2>/dev/null || return 1
    "$NTSILK_BIN" -hide_banner -muxers 2>/dev/null | grep -qi ntsilk
}

# 系统 ffmpeg 本身就支持时无需改动
if "$FFMPEG_BIN" -hide_banner -muxers 2>/dev/null | grep -qi ntsilk; then
    say "系统 ffmpeg 已支持 ntsilk，无需处理"
    exit 0
fi

if is_ready; then
    say "已修复：ffmpeg 支持 ntsilk，无需处理"
    exit 0
fi

if [ "$CHECK_ONLY" = "1" ]; then
    say "未修复：NapCat 发语音会失败（系统 ffmpeg 缺 ntsilk）"
    exit 1
fi

if [ "$(id -u)" != "0" ]; then
    say "需要 root 权限（会修改 $FFMPEG_BIN 和 $FFPROBE_BIN）"
    say "请进入 NapCat 容器后以 root 执行，例如：docker exec -it -u root <napcat容器名> sh ..."
    exit 1
fi

ARCH=$(uname -m 2>/dev/null || echo unknown)
case "$ARCH" in
    x86_64 | amd64)
        URL_PRIMARY='https://ntsilk.ilharper.com/ntsilk-linux-x64'
        ;;
    *)
        say "当前架构 $ARCH 没有可直接下载的 ntsilk 二进制"
        say "请改用带 Native Addon 的 NapCat 版本/镜像，或自行编译支持 ntsilk 的 ffmpeg"
        exit 1
        ;;
esac
URL_FALLBACK='https://github.com/ntsilk-userland/binaries/releases/latest'

TMP=$(mktemp /tmp/ntsilk.XXXXXX) || exit 1
cleanup() {
    rm -f "$TMP"
}
trap cleanup EXIT INT TERM

say "下载 ntsilk ffmpeg..."
if ! curl -fsSL --max-time 180 -o "$TMP" "$URL_PRIMARY"; then
    say "主地址下载失败；请从 $URL_FALLBACK 手动下载 ntsilk-linux-x64 后重试"
    exit 1
fi
chmod +x "$TMP"

if ! "$TMP" -hide_banner -muxers 2>/dev/null | grep -qi ntsilk; then
    say "下载到的二进制不包含 ntsilk_s16le，已停止"
    exit 1
fi

mkdir -p /usr/local/bin
mv "$TMP" "$NTSILK_BIN"
chmod +x "$NTSILK_BIN"
trap - EXIT INT TERM
say "已安装 $NTSILK_BIN"

# 保留原始 ffmpeg/ffprobe
if [ ! -f "$FFMPEG_BIN.real" ]; then
    cp -a "$FFMPEG_BIN" "$FFMPEG_BIN.real"
fi

cat > "$FFMPEG_BIN" <<EOF
#!/bin/sh
$MARK
case "\$*" in
    *ntsilk*) exec $NTSILK_BIN "\$@" ;;
esac
exec $FFMPEG_BIN.real "\$@"
EOF
chmod +x "$FFMPEG_BIN"

if [ ! -f "$FFPROBE_BIN.real" ]; then
    cp -a "$FFPROBE_BIN" "$FFPROBE_BIN.real"
fi

cat > "$FFPROBE_BIN" <<EOF
#!/bin/sh
$MARK
$FFPROBE_BIN.real "\$@"
rc=\$?
[ \$rc -eq 0 ] && exit 0
# 系统 ffprobe 无法读取 silk 时，按腾讯 SILK 的常见码率估算时长
f=''
for a in "\$@"; do
    case "\$a" in
        -*) continue ;;
    esac
    [ -f "\$a" ] && f="\$a"
done
[ -n "\$f" ] || exit \$rc
head -c 10 "\$f" 2>/dev/null | grep -q SILK || exit \$rc
sz=\$(stat -c %s "\$f" 2>/dev/null) || exit \$rc
whole=\$((sz / 3466))
frac=\$(( (sz % 3466) * 1000 / 3466 ))
printf '%d.%03d\n' "\$whole" "\$frac"
exit 0
EOF
chmod +x "$FFPROBE_BIN"

say "修复完成：ffmpeg 分发到 ntsilk，ffprobe 增加 silk 时长兜底"
say "请重启 NapCat 容器后重新发送语音"
