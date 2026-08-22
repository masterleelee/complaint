#!/bin/bash
# 驾校投诉处理系统 一键启动脚本
# 双击运行即可，服务后台常驻，关闭窗口不影响服务

cd "$(dirname "$0")"

PORT=5003
PID_FILE="/tmp/complaint_system.pid"

echo "========================================"
echo "  驾校投诉处理系统"
echo "========================================"
echo ""

# 检查端口是否已被占用（服务已在运行）
if lsof -i :$PORT &>/dev/null; then
    echo "✅ 服务已在运行: http://127.0.0.1:$PORT"
    echo "   如需停止: kill \$(cat $PID_FILE)"
    open "http://127.0.0.1:$PORT"
    exit 0
fi

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python3，请先安装 Python 3.10+"
    exit 1
fi

echo "✅ Python: $(python3 --version)"

# 检查并安装依赖
if [ ! -d "venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate

if [ ! -f "venv/.deps_installed" ]; then
    echo "📦 安装依赖..."
    pip install -r requirements.txt -q
    touch venv/.deps_installed
    echo "✅ 依赖安装完成"
else
    echo "✅ 依赖已就绪"
fi

echo ""
echo "🚀 后台启动服务..."
echo "   访问地址: http://127.0.0.1:$PORT"
echo "   关闭本窗口不影响服务运行"
echo "   如需停止: kill \$(cat $PID_FILE)"
echo ""

# nohup 后台启动，关闭终端窗口不会中断服务
nohup python3 app.py >> /tmp/complaint_system.log 2>&1 &
echo $! > "$PID_FILE"

sleep 2
if lsof -i :$PORT &>/dev/null; then
    echo "✅ 服务启动成功"
    open "http://127.0.0.1:$PORT"
else
    echo "❌ 启动失败，查看日志: /tmp/complaint_system.log"
    tail -20 /tmp/complaint_system.log
fi
