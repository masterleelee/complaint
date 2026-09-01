#!/bin/bash
# 驾校投诉处理系统 - 启动入口
# 双击运行即可，三级策略依次降级：
#   策略 1  端口已在监听   → 服务本来就在跑，直接开浏览器
#   策略 2  launchd 接管   → 开机自启 + 崩溃自愈（最理想）
#   策略 3  nohup 兜底     → launchd 不可用时直接起进程
#
# 手动停止：
#   lsof -ti :5003 | xargs kill
# 若服务由 launchd 托管（策略 2），需先卸载再停：
#   launchctl unload ~/Library/LaunchAgents/com.complaint.system.plist

cd "$(dirname "$0")"

PORT=5003
PLABEL="com.complaint.system"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLABEL.plist"
LOG="/tmp/complaint_system.log"
PYTHON="./venv/bin/python3"

echo "========================================"
echo "  驾校投诉处理系统"
echo "========================================"
echo ""

port_up() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN &>/dev/null; }

show_url() {
    LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
    echo "   本机访问: http://127.0.0.1:$PORT"
    [ -n "$LAN_IP" ] && echo "   局域网访问: http://$LAN_IP:$PORT"
}

# ---- 策略 1：端口已在监听，说明服务本来就在跑 ----
# 注意：端口被占用是"服务已就绪"的正常状态，不是错误。
if port_up; then
    echo "ℹ️  端口 $PORT 已在监听，服务已在运行"
    if launchctl list 2>/dev/null | grep -q "$PLABEL"; then
        echo "   当前由 launchd 托管（开机自启 + 崩溃自愈）"
    fi
    show_url
    open "http://127.0.0.1:$PORT"
    exit 0
fi

# ---- 策略 2：交给 launchd（开机自启 + 崩溃自愈）----
if [ -f "$PLIST_PATH" ]; then
    if launchctl list 2>/dev/null | grep -q "$PLABEL"; then
        echo "ℹ️  launchd 任务已注册，触发重启..."
        launchctl kickstart -k "gui/$(id -u)/$PLABEL" 2>/dev/null
    else
        echo "ℹ️  尝试用 launchd 启动（开机自启 + 崩溃自愈）..."
        launchctl load -w "$PLIST_PATH" 2>/dev/null
    fi

    echo "   等待服务就绪（最多 40 秒）..."
    for _ in $(seq 1 40); do
        port_up && break
        sleep 1
    done

    if port_up; then
        echo "✅ launchd 已接管服务"
        show_url
        open "http://127.0.0.1:$PORT"
        exit 0
    fi

    # launchd 起不来时务必卸载，否则 KeepAlive 会每 10 秒崩溃重启一次，
    # 既刷日志又会和策略 3 抢端口。
    echo "⚠️  launchd 未能启动服务，卸载任务，改用直接启动..."
    launchctl unload "$PLIST_PATH" 2>/dev/null
else
    echo "ℹ️  未找到 launchd 配置（$PLIST_PATH），使用直接启动方式"
fi

# ---- 策略 3：nohup 兜底 ----
# env -u PYTHONPATH 不可省略：某些终端环境注入的 PYTHONPATH 会劫持
# pathlib.Path.mkdir，导致进程崩在 config.py 的 _ensure_dirs()。
echo "ℹ️  直接启动服务（nohup 后台，关闭终端不受影响）..."
nohup env -u PYTHONPATH "$PYTHON" app.py >> "$LOG" 2>&1 &
NEW_PID=$!
echo "   PID=$NEW_PID，等待服务就绪（torch 加载较慢，最多 90 秒）..."

for _ in $(seq 1 90); do
    port_up && break
    sleep 1
done

if port_up; then
    echo "✅ 服务已启动（PID=$NEW_PID）"
    show_url
    open "http://127.0.0.1:$PORT"
else
    echo "❌ 启动失败，日志尾部如下："
    tail -30 "$LOG" 2>/dev/null
    exit 1
fi
