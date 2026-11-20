#!/bin/bash
# 打开「量化研究」时拉起页面服务。stop 只结束 pid 文件里记下的进程。
set -u

ROOT="${QUANT_ROOT:-/Users/meng/测试/量化工具}"
PY="${QUANT_PY:-/Users/meng/测试/.venv/bin/python}"
APP="${QUANT_APP:-$ROOT/app.py}"
PORT="${QUANT_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"
LOG_DIR="$ROOT/logs"
PIDFILE="$LOG_DIR/quant.pid"
APP_PIDFILE="$LOG_DIR/quant_app.pid"
LOG="$LOG_DIR/app.log"

mkdir -p "$LOG_DIR"

http_up() {
  curl -sf -o /dev/null --max-time 1 "$URL"
}

listener_pid() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | awk 'NR==1 { print; exit }'
}

is_ours() {
  local pid="$1"
  local cmd base
  [ -n "$pid" ] || return 1
  cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
  base=$(basename "$APP")
  [[ "$cmd" == *"$base"* ]]
}

alert() {
  osascript -e "display alert \"量化研究\" message \"$1\" as warning" >/dev/null 2>&1 || true
}

cmd_status() {
  if http_up; then
    echo up
  else
    echo down
  fi
}

cmd_stop() {
  [ -f "$PIDFILE" ] || return 0
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  rm -f "$PIDFILE"
  [ -n "${pid:-}" ] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    local _
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 0.1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
}

cmd_ensure() {
  if http_up; then
    local pid
    pid=$(listener_pid || true)
    if is_ours "$pid"; then
      echo "$pid" >"$PIDFILE"
      return 0
    fi
    echo "端口 ${PORT} 已被其它程序占用" >&2
    return 1
  fi
  if [ ! -x "$PY" ]; then
    echo "找不到 Python: $PY" >&2
    return 1
  fi
  if [ ! -f "$APP" ]; then
    echo "找不到入口: $APP" >&2
    return 1
  fi
  export QUANT_PORT="$PORT"
  export QUANT_NO_BROWSER=1
  printf '\n==== %s 启动 ====\n' "$(date '+%Y-%m-%d %H:%M:%S')" >>"$LOG"
  nohup "$PY" "$APP" >>"$LOG" 2>&1 &
  local pid=$!
  echo "$pid" >"$PIDFILE"
  local _
  for _ in $(seq 1 100); do
    if http_up; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "页面服务启动后退出，见 $LOG" >&2
      return 1
    fi
    sleep 0.2
  done
  echo "页面服务在 20 秒内没有打开，见 $LOG" >&2
  return 1
}

cmd_run() {
  if [ -f "$APP_PIDFILE" ]; then
    local existing
    existing=$(cat "$APP_PIDFILE" 2>/dev/null || true)
    if [ -n "${existing:-}" ] && [ "$existing" != "$$" ] && kill -0 "$existing" 2>/dev/null; then
      if [ "${QUANT_NO_OPEN:-}" != "1" ]; then
        open "$URL"
      fi
      return 0
    fi
  fi
  echo $$ >"$APP_PIDFILE"
  cleanup() {
    trap - TERM INT
    cmd_stop
    rm -f "$APP_PIDFILE"
  }
  trap 'cleanup; exit 0' TERM INT
  if ! cmd_ensure; then
    alert "页面服务没起来。日志：$LOG"
    cleanup
    return 1
  fi
  if [ "${QUANT_NO_OPEN:-}" != "1" ]; then
    open "$URL"
  fi
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  while [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    pid=$(cat "$PIDFILE" 2>/dev/null || true)
  done
  cleanup
}

usage() {
  echo "用法: quant_app.sh ensure|stop|status|run" >&2
  return 2
}

cmd="${1:-}"
case "$cmd" in
  ensure) cmd_ensure ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  run) cmd_run ;;
  *) usage ;;
esac
