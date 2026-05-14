#!/bin/bash
# 驾校投诉处理系统 一键启动脚本
# 双击运行即可

cd "$(dirname "$0")"

echo "========================================"
echo "  驾校投诉处理系统"
echo "========================================"
echo ""

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

# 安装依赖（如果未安装）
if [ ! -f "venv/.deps_installed" ]; then
    echo "📦 安装依赖..."
    pip install -r requirements.txt -q
    touch venv/.deps_installed
    echo "✅ 依赖安装完成"
else
    echo "✅ 依赖已就绪"
fi

echo ""
echo "🚀 启动服务..."
echo "   访问地址: http://127.0.0.1:5003"
echo "   按 Ctrl+C 停止服务"
echo ""

python3 app.py
