#!/bin/bash
# init.sh — 初始化开发环境
# 每次新 session 开始时运行

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPS="root@76.13.191.45"
VPS_PATH="/var/www/audiototext-api"

echo "=== OskrisTranscribe Auth System 开发环境 ==="
echo "项目目录: $PROJECT_DIR"

# 1. 确认 VPS 连接
echo ""
echo "--- 检查 VPS 连接 ---"
if ssh -o ConnectTimeout=5 -o BatchMode=yes $VPS "echo 'VPS OK'" 2>/dev/null; then
    echo "✅ VPS 连接正常"
else
    echo "❌ VPS 连接失败，请检查 SSH 配置"
    exit 1
fi

# 2. 检查后端服务
echo ""
echo "--- 检查 API 服务 ---"
API_HEALTH=$(curl -s https://api.oskris.com/health 2>/dev/null)
if echo "$API_HEALTH" | grep -q "ok"; then
    echo "✅ API 服务正常: $API_HEALTH"
else
    echo "⚠️ API 服务异常，尝试重启..."
    ssh $VPS "cd $VPS_PATH && pkill -f uvicorn || true && sleep 1 && \
        nohup ./venv/bin/uvicorn server:app --host 127.0.0.1 --port 7878 --reload \
        > /tmp/uvicorn.log 2>&1 &"
    sleep 3
    echo "API: $(curl -s https://api.oskris.com/health)"
fi

# 3. 拉取最新代码到本地
echo ""
echo "--- 同步 VPS → 本地 ---"
scp $VPS:$VPS_PATH/server.py "$PROJECT_DIR/server.py" 2>/dev/null || \
    cp "$PROJECT_DIR/server_current.py" "$PROJECT_DIR/server.py"
echo "✅ 已拉取 server.py"

# 4. 安装本地 Python 依赖（用于测试）
echo ""
echo "--- 检查 Python 依赖 ---"
if ! python3 -c "import fastapi, sqlite3, jwt, bcrypt" 2>/dev/null; then
    echo "安装依赖..."
    pip3 install fastapi uvicorn python-jose[cryptography] passlib[bcrypt] \
        python-multipart httpx google-auth 2>/dev/null | tail -3
fi
echo "✅ 依赖就绪"

# 5. 显示项目状态
echo ""
echo "=== 项目状态 ==="
echo "spec: $PROJECT_DIR/app_spec.txt"
echo "feature_list: $PROJECT_DIR/feature_list.json"
echo "progress: $PROJECT_DIR/claude-progress.txt"
echo ""
if [ -f "$PROJECT_DIR/feature_list.json" ]; then
    total=$(cat "$PROJECT_DIR/feature_list.json" | grep -c '"passes"' 2>/dev/null || echo 0)
    passing=$(cat "$PROJECT_DIR/feature_list.json" | grep -c '"passes": true' 2>/dev/null || echo 0)
    echo "进度: $passing / $total 测试通过"
fi
echo ""
echo "✅ 环境准备完成，可以开始开发！"
