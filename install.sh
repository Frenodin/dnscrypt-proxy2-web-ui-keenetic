#!/opt/bin/busybox sh
#
# install.sh — установщик веб-панели dnscrypt-proxy2 для Keenetic/Entware.
# https://github.com/Frenodin/dnscrypt-proxy2-web-ui-keenetic
#
# Использование:
#   wget -O install.sh https://raw.githubusercontent.com/Frenodin/dnscrypt-proxy2-web-ui-keenetic/main/install.sh
#   chmod +x install.sh
#   ./install.sh
#
# Скрипт идемпотентен — безопасно запускать повторно (например, чтобы
# обновить server.py до последней версии): существующий пароль в
# panel-auth.json не будет перезаписан.

set -e

REPO_RAW="https://raw.githubusercontent.com/Frenodin/dnscrypt-proxy2-web-ui-keenetic/main"
INSTALL_DIR="/opt/etc/dnscrypt-proxy2-ui"
INIT_SCRIPT="/opt/etc/init.d/S99dnscrypt-panel"
AUTH_FILE="$INSTALL_DIR/panel-auth.json"
PANEL_PORT=8091

echo "=== dnscrypt-proxy2 web panel — установка ==="

# --- Проверка окружения ---
if ! command -v opkg >/dev/null 2>&1; then
    echo "ОШИБКА: opkg не найден. Этот скрипт рассчитан на Entware (Keenetic/OpenWrt-подобные роутеры)."
    exit 1
fi

echo "--- Проверка зависимостей ---"
opkg update >/dev/null 2>&1 || true

if ! command -v python3 >/dev/null 2>&1; then
    echo "Устанавливаю python3..."
    opkg install python3 python3-light
else
    echo "python3 уже установлен: $(python3 --version 2>&1)"
fi

if ! command -v dnscrypt-proxy >/dev/null 2>&1; then
    echo "Устанавливаю dnscrypt-proxy2..."
    opkg install dnscrypt-proxy2
else
    echo "dnscrypt-proxy2 уже установлен."
fi

if [ ! -f /opt/etc/dnscrypt-proxy.toml ]; then
    echo "ВНИМАНИЕ: /opt/etc/dnscrypt-proxy.toml не найден."
    echo "Панель установится, но не будет работать, пока вы не настроите dnscrypt-proxy2."
    echo "См. инструкцию: $REPO_RAW/README.md"
fi

# --- Скачивание server.py ---
echo "--- Загрузка панели ---"
mkdir -p "$INSTALL_DIR"

if command -v wget >/dev/null 2>&1; then
    wget -q -O "$INSTALL_DIR/server.py" "$REPO_RAW/server.py"
elif command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$INSTALL_DIR/server.py" "$REPO_RAW/server.py"
else
    echo "ОШИБКА: нужен wget или curl для скачивания server.py."
    exit 1
fi

if [ ! -s "$INSTALL_DIR/server.py" ]; then
    echo "ОШИБКА: server.py не скачался (пустой файл). Проверьте интернет-соединение роутера."
    exit 1
fi

python3 -m py_compile "$INSTALL_DIR/server.py"
echo "server.py загружен и прошёл проверку синтаксиса."

# --- Учётные данные ---
if [ -f "$AUTH_FILE" ]; then
    echo "panel-auth.json уже существует — пароль сохранён, не трогаю."
else
    echo "--- Генерация пароля ---"
    GENERATED_PASS=$(head -c 12 /dev/urandom | base64 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c 16)
    if [ -z "$GENERATED_PASS" ]; then
        # запасной вариант, если base64/tr недоступны в нужном виде
        GENERATED_PASS=$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')
    fi
    cat > "$AUTH_FILE" << AUTHEOF
{"user": "admin", "pass": "$GENERATED_PASS"}
AUTHEOF
    chmod 600 "$AUTH_FILE"
    echo "Создан новый пароль администратора."
fi

# --- Init.d автозапуск ---
echo "--- Настройка автозапуска ---"
cat > "$INIT_SCRIPT" << 'INITEOF'
#!/opt/bin/busybox sh
PATH=/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin
SCRIPT=/opt/etc/dnscrypt-proxy2-ui/server.py
PIDFILE=/opt/var/run/dnscrypt-panel.pid
LOGFILE=/opt/var/log/dnscrypt-panel.log

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "dnscrypt-panel already running"
        return 0
    fi
    echo "Starting dnscrypt-panel..."
    /opt/bin/python3 "$SCRIPT" >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
}

stop() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE"
        echo "Stopped dnscrypt-panel"
    else
        echo "dnscrypt-panel not running"
    fi
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    *) echo "Usage: $0 {start|stop|restart}" ;;
esac
INITEOF
chmod +x "$INIT_SCRIPT"

echo "--- Запуск ---"
"$INIT_SCRIPT" restart
sleep 2

ROUTER_IP=$(ip addr show br0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1)
[ -z "$ROUTER_IP" ] && ROUTER_IP="<IP роутера>"

echo ""
echo "=== Установка завершена ==="
echo "Панель доступна: http://$ROUTER_IP:$PANEL_PORT"
if [ -n "${GENERATED_PASS:-}" ]; then
    echo "Логин: admin"
    echo "Пароль: $GENERATED_PASS"
    echo "(сохранён в $AUTH_FILE, смените его через вкладку «Настройки» в панели)"
fi
