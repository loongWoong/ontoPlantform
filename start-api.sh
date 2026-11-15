#!/usr/bin/env bash
# OntoPlatform 启动脚本（API + Web 控制台）· macOS / Linux
#
#   ./start-api.sh               # 默认 127.0.0.1:8100，库 .onto/platform.db
#   ./start-api.sh -p 8101       # 换端口
#   ./start-api.sh -H 0.0.0.0    # 监听所有网卡
#   PORT=8200 ./start-api.sh     # 也支持环境变量
#
# 虚拟环境默认取仓库上一级的 .venv-onto，可用 VENV_DIR 覆盖。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# ---- 参数解析（命令行优先，其次环境变量，最后默认值）----
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8100}"
DB="${DB:-.onto/platform.db}"
while [ $# -gt 0 ]; do
  case "$1" in
    -H|--host) HOST="$2"; shift 2 ;;
    -p|--port) PORT="$2"; shift 2 ;;
    -d|--db)   DB="$2";   shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（用 -h 查看用法）" >&2; exit 2 ;;
  esac
done

# ---- 启用虚拟环境 ----
VENV_DIR="${VENV_DIR:-${REPO_DIR}/../.venv-onto}"
if [ ! -f "${VENV_DIR}/bin/activate" ]; then
  echo "未找到虚拟环境：${VENV_DIR}" >&2
  echo "请先创建并安装依赖：" >&2
  echo "  /opt/homebrew/bin/python3.12 -m venv ${VENV_DIR}" >&2
  echo "  ${VENV_DIR}/bin/pip install -e \"${REPO_DIR}[dev]\" --no-build-isolation -i https://mirrors.aliyun.com/pypi/simple/" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# ---- LLM（OpenAI 兼容协议；已有同名环境变量时以环境变量为准）----
: "${ONTO_LLM_BASE_URL:=http://127.0.0.1:8000/v1}"
: "${ONTO_LLM_MODEL:=deepseek-v4-flash}"
: "${ONTO_LLM_API_KEY:=sk-123}"
export ONTO_LLM_BASE_URL ONTO_LLM_MODEL ONTO_LLM_API_KEY

echo "onto-api  http://${HOST}:${PORT}   db=${DB}"
echo "python    $(python --version 2>&1)   venv=${VENV_DIR}"
echo "llm       ${ONTO_LLM_MODEL} @ ${ONTO_LLM_BASE_URL}"

exec python -m onto_platform.interfaces.api \
  --db "${DB}" \
  --host "${HOST}" \
  --port "${PORT}"
