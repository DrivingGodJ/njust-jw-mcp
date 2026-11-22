#!/usr/bin/env bash
# 刷新教务系统 cookie（用浏览器，靠浏览器里已有的 CASTGC 自动 SSO）
#
# 用法: scripts/jw-capture.sh ['任务空间名'] ['输出路径']
# 输出: 默认 ~/.config/njust/jw-cookie.json（600）
#
# 说明：教务系统 bkjw 自己那个登录页强制验证码，但走 SSO 进去不需要。
# 浏览器里 CASTGC 还在的话，打开课表页就是已登录状态，这里只是把 cookie 抄出来。
# 只有 CASTGC 也过期时才需要人工登录一次。

set -euo pipefail

SPACE="${1:-南理工教务}"
OUT="${2:-$HOME/.config/njust/jw-cookie.json}"

EGO="${EGO_BROWSER_BIN:-$HOME/.local/bin/ego-browser}"
[ -x "$EGO" ] || { echo "找不到 ego-browser：$EGO" >&2; exit 1; }

DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$(dirname "$OUT")"; chmod 700 "$(dirname "$OUT")" 2>/dev/null || true

TMP="$(mktemp /tmp/jw-cap-XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT

BEFORE=0
[ -f "$OUT" ] && BEFORE=$(stat -f %m "$OUT" 2>/dev/null || echo 0)

{
  echo "const config = {"
  echo "  space: $(printf '%s' "$SPACE" | /usr/bin/python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),"
  echo "  out: $(printf '%s' "$OUT" | /usr/bin/python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),"
  echo "  url: \"http://bkjw.njust.edu.cn/njlgdx/xskb/xskb_list.do?Ves632DSdyV=NEW_XSD_PYGL\""
  echo "};"
  cat "$DIR/jw-capture.js"
} > "$TMP"

"$EGO" nodejs < "$TMP"

AFTER=0
[ -f "$OUT" ] && AFTER=$(stat -f %m "$OUT" 2>/dev/null || echo 0)

if [ "$AFTER" = "$BEFORE" ]; then
  echo "捕获失败：cookie 文件未刷新（可能需要人工登录教务系统一次）" >&2
  exit 1
fi

chmod 600 "$OUT"
echo "已写入: $OUT"
