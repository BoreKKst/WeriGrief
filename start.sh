#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/WeriGrief"

while true; do
    echo "[$(date)] Запуск WeriGrief..."
    python main.py
    CODE=$?
    echo "[$(date)] Бот остановился (код $CODE). Перезапуск через 10 секунд..."
    sleep 10
done
