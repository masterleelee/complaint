#!/bin/bash
# macOS 防火墙放行投诉处理系统
# 用法：bash scripts/setup_firewall.sh
# 作用：在 系统设置 → 网络 → 防火墙 → 选项 里，添加 venv/bin/python3
#       并允许传入连接（TCP 5003）。
# 取消放行：bash scripts/setup_firewall.sh --remove

set -e

PYTHON_BIN="/Users/master/Desktop/投诉处理系统/venv/bin/python3"
SOCKET_FILTER_PLIST="/Library/LaunchDaemons/com.kjjx.complaint.socketfilter.plist"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "❌ 找不到 $PYTHON_BIN"
    echo "   请先在项目根目录创建 venv: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

remove_rule() {
    echo "🔧 移除 socket 过滤规则 (需要管理员密码)..."
    if [ -f "$SOCKET_FILTER_PLIST" ]; then
        sudo /usr/libexec/ApplicationFirewall/socketfilterfw --remove "$SOCKET_FILTER_PLIST" 2>/dev/null || true
        sudo rm -f "$SOCKET_FILTER_PLIST"
    fi
    # 直接删除该可执行文件的放行授权
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --remove "$PYTHON_BIN" 2>/dev/null || true
    echo "✅ 已移除放行规则（如有）"
}

add_rule() {
    echo "🔧 添加 socket 过滤规则 (需要管理员密码)..."
    echo "   程序: $PYTHON_BIN"
    echo "   端口: 5003 (TCP)"

    # 方式 1：直接用 --add 把可执行文件加进防火墙白名单
    # 这是 macOS 10.10+ 推荐的 API，不需要手写 plist
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$PYTHON_BIN"
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblock "$PYTHON_BIN"

    # 兜底：手写 plist（极少数老 macOS 才有此需要；放 /Library/LaunchDaemons 不会自启，
    # 只是给 socketfilterfw 看的注册表项）
    cat <<PLIST | sudo tee "$SOCKET_FILTER_PLIST" >/dev/null
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kjjx.complaint.socketfilter</string>
    <key>Program</key>
    <string>$PYTHON_BIN</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>/Users/master/Desktop/投诉处理系统/app.py</string>
    </array>
    <key>SocketFilter</key>
    <dict>
        <key>AllowIncoming</key>
        <true/>
        <key>AllowOutgoing</key>
        <true/>
    </dict>
</dict>
</plist>
PLIST
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$SOCKET_FILTER_PLIST" 2>/dev/null || true
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblock "$SOCKET_FILTER_PLIST" 2>/dev/null || true

    # 显示当前规则状态
    echo ""
    echo "📋 当前防火墙全局状态:"
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
    echo ""
    echo "📋 该程序的入站规则:"
    sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getblocked "$PYTHON_BIN" 2>&1 || true
    echo ""
    echo "✅ 防火墙放行已配置完成"
    echo ""
    echo "👉 接下来请验证："
    echo "   1. 在本机浏览器打开: http://192.168.1.192:5003"
    echo "   2. 让同事在另一台电脑浏览器打开: http://192.168.1.192:5003"
    echo "   3. 如果同事访问被拒，但本机正常："
    echo "      系统设置 → 网络 → 防火墙 → 选项 → 找到 /Users/master/Desktop/投诉处理系统/venv/bin/python3"
    echo "      把它的权限从「拒绝传入连接」改为「允许传入连接」"
}

case "${1:-add}" in
    add|"") add_rule ;;
    remove|rm|--remove) remove_rule ;;
    *)
        echo "用法: $0 [add|remove]"
        exit 1
        ;;
esac
