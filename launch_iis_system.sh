#!/bin/bash
set -u   # 遇到未定义变量就退出，提前暴露错误

# [DEPRECATED] 本脚本仅用于本地手工调试。
# 生产环境请使用 systemd/ 目录下的独立服务单元：
#   iis-vectordb.service, iis-web.service, iis-crawler.service, iis.target
# 参见 systemd/README.md。

# ========= 基本配置 =========
PROJECT_DIR="/home/sleepy/Documents/IntelligenceIntegrationSystem"
LOG_DIR="$PROJECT_DIR/_log"
PYTHON="/home/sleepy/anaconda3/envs/iis/bin/python"

# 确保日志目录存在
mkdir -p "$LOG_DIR" || { echo "无法创建日志目录 $LOG_DIR"; exit 1; }

# 进入项目目录，失败则退出
cd "$PROJECT_DIR" || { echo "无法进入目录 $PROJECT_DIR"; exit 1; }

# ========= 环境变量 =========
export PYTHONPATH="$PROJECT_DIR"
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export NUMEXPR_NUM_THREADS=4
export TOKENIZERS_PARALLELISM=false
export CUDA_VISIBLE_DEVICES=""

# ========= 辅助函数 =========
# 用于存储所有后台 PID
pids=()

start_app() {
    local name="$1"
    local cmd="$2"
    local log_file="$LOG_DIR/$3"

    echo "[$(date)] Starting $name"
    # 使用 bash -c 执行命令，确保重定向正确
    bash -c "$cmd" > "$log_file" 2>&1 &
    pids+=($!)
}

cleanup() {
    echo "[$(date)] Caught signal or child exited, stopping all apps..."
    for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
    done
    wait   # 等待所有子进程退出
    echo "[$(date)] All apps stopped."
    exit 0
}

# 捕获系统信号，优雅停止所有子进程
trap cleanup SIGTERM SIGINT

# ========= 启动三个服务 =========
start_app "Vector DB" \
    "$PYTHON -m VectorDB.VectorDBBService \
     --db-path $PROJECT_DIR/_data/VectorDB \
     --model /home/sleepy/Documents/bge-m3" \
    "linux_vector_db.log"

start_app "IIS" \
    "$PYTHON IntelligenceHubLauncher.py" \
    "linux_iis.log"

start_app "Crawl Engine" \
    "$PYTHON CrawlerServiceEngine.py" \
    "linux_crawl_engine.log"

# ========= 监控子进程 =========
echo "[$(date)] All services started. Waiting for any to exit..."
wait -n

# 任一子进程退出（无论正常还是异常），都调用 cleanup 清理并退出
echo "[$(date)] A child process exited, initiating shutdown..."
cleanup

