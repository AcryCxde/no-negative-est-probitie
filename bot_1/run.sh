#!/bin/sh
# Держит бота запущенным: не даёт Mac уснуть и перезапускает процесс после падения.
# Остановка: Ctrl+C или SIGTERM — бот доиграет идущую партию. Код 1/2 — фатальный отказ, не перезапускаем.
cd "$(dirname "$0")/.." || exit 2
while true; do
    caffeinate -ims uv run python -m bot_1
    code=$?
    case "$code" in
        0|1|2) exit "$code" ;;
    esac
    echo "бот завершился с кодом $code, перезапуск через 5 с" >&2
    sleep 5
done
