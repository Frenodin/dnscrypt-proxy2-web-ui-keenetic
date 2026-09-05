# dnscrypt-proxy2 Web UI для Keenetic

Лёгкая веб-панель для управления [dnscrypt-proxy2](https://github.com/DNSCrypt/dnscrypt-proxy) на роутерах Keenetic (Entware). Написана на чистом Python 3 стандартная библиотека, без Flask — специально для устройств с ограниченными ресурсами.

Решает практическую проблему: dnscrypt-proxy2 настраивается через `dnscrypt-proxy.toml`, и без веб-интерфейса каждое изменение смена ODoH-relay, target-сервера, кеша требует ручного редактирования файла через SSH и рестарта сервиса вслепую. Эта панель даёт то же самое из браузера, с проверкой конфига.

## Возможности

- **Дашборд** — статус процесса работает/остановлен, PID, текущий ODoH target и relay, RTT последнего успешного запроса
- **Управление сервисом** — Start / Stop / Restart одной кнопкой
- **Relay / Target** — выбор ODoH-сервера и одного или нескольких relay из актуальных списков [DNSCrypt/dnscrypt-resolvers](https://github.com/DNSCrypt/dnscrypt-resolvers) без ручного редактирования toml
- **Редактор конфига** — полный текст `dnscrypt-proxy.toml` прямо в браузере, с **проверкой перед сохранением**: панель запускает тестовый экземпляр dnscrypt-proxy на отдельном порту и не даст сохранить конфиг, если в нём синтаксическая ошибка или недоступный сервер/relay
- **Автоматический бэкап** — перед каждым сохранением создаётся копия текущего конфига хранятся последние 20
- **Логи** — живой хвост лога dnscrypt-proxy прямо в интерфейсе
- **Смена пароля** — из самой панели, без правки скрипта; учётные данные хранятся отдельно от кода в `panel-auth.json`

## Быстрая установка

```sh
wget -O install.sh https://raw.githubusercontent.com/Frenodin/dnscrypt-proxy2-web-ui-keenetic/main/install.sh
chmod +x install.sh
./install.sh
```

Скрипт сам проверит/поставит зависимости (python3, dnscrypt-proxy2), скачает панель, сгенерирует случайный пароль администратора и настроит автозапуск. В конце выведет ссылку и пароль. Безопасно запускать повторно — существующий пароль не будет сброшен.

Требуется отдельно настроенный `dnscrypt-proxy.toml` (ODoH target + relay) — см. пример конфигурации ниже, панель сама его не создаёт.

## Требования

- Keenetic с Entware
- Установленный `dnscrypt-proxy2` (`opkg install dnscrypt-proxy2`)
- `python3` (`opkg install python3 python3-light`)

## Установка

```sh
mkdir -p /opt/etc/dnscrypt-proxy2-ui
# скопируйте server.py из этого репозитория в /opt/etc/dnscrypt-proxy2-ui/server.py

python3 -m py_compile /opt/etc/dnscrypt-proxy2-ui/server.py && echo "OK"
```

Создайте файл с учётными данными (замените пароль на свой):

```sh
cat > /opt/etc/dnscrypt-proxy2-ui/panel-auth.json << 'EOF'
{"user": "admin", "pass": "ВАШ_ПАРОЛЬ"}
EOF
chmod 600 /opt/etc/dnscrypt-proxy2-ui/panel-auth.json
```

Init.d скрипт для автозапуска `/opt/etc/init.d/S99dnscrypt-panel`:

```sh
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
```

```sh
chmod +x /opt/etc/init.d/S99dnscrypt-panel
/opt/etc/init.d/S99dnscrypt-panel start
```

Откройте `http://<IP роутера>:8091` — логин/пароль из `panel-auth.json`.

## Безопасность

- Панель слушает `0.0.0.0` — доступна с любого устройства в вашей локальной сети. Не открывайте порт 8091 на Wинтернет без дополнительной защиты.
- Basic Auth передаётся без шифрования обычный HTTP внутри LAN — приемлемо для домашней сети с доверенными устройствами, не подходит для сетей с посторонними/гостевыми подключениями без дополнительных мер.
- `panel-auth.json` хранит пароль в открытом виде, доступ к файлу ограничен правами `chmod 600`.

## Как это работает

Панель — HTTP-сервер на стандартной библиотеке `http.server` + `socketserver.ThreadingTCPServer`, management-команды выполняются через `subprocess` вызовом init.d скрипта dnscrypt-proxy2, конфиг читается/пишется напрямую как текст. Валидация перед сохранением — реальный тестовый запуск `dnscrypt-proxy -config <файл>` на отдельном 

## Лицензия

MIT (см. [LICENSE](LICENSE))
