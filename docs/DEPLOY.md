# Развёртывание на сервере

Панель — один процесс Streamlit и файл SQLite. Внешние сервисы (СУБД, очередь,
кэш) не нужны. Ниже два способа запуска (Docker Compose и systemd), настройка
обратного прокси, параметры и типичные неполадки.

## Требования

- Linux-сервер: 2 vCPU и 2 ГБ памяти (замеры — в конце документа).
- Вариант A — Docker Engine с плагином Compose v2. Вариант B — Python 3.11
  и systemd. Зависимости проверены на Python 3.11; на других версиях проект
  не запускался.
- Для HTTPS — домен, направленный на сервер, и открытые порты 80 и 443.

## Что учесть заранее

- В панели нет авторизации. Закройте доступ паролем в nginx (ниже), через VPN
  или фильтром по IP.
- Вкладка «Симуляция» считает внутри процесса сервера и пересоздаёт общую базу
  (`HSR_DB_URL`). Панель рассчитана на одного оператора: два одновременных
  прогона мешают друг другу.
- Вкладки «Обзор», «Сравнение», «Аналитика» и «Станок» читают результаты из
  `data/last_run/`. Эти файлы входят в репозиторий и образ, пересчёт на вкладке
  «Симуляция» их не меняет.

## Вариант A. Docker Compose (рекомендуется)

```bash
git clone <адрес репозитория> hsr && cd hsr
cp .env.example .env              # при необходимости отредактировать
docker compose up -d --build
docker compose ps                 # через минуту статус должен стать healthy
curl -fsS http://127.0.0.1:8501/_stcore/health    # ответ: ok
```

По умолчанию порт публикуется только на `127.0.0.1` — панель доступна через
nginx (см. ниже). Для проверки без nginx задайте в `.env` `HSR_BIND=0.0.0.0`,
выполните `docker compose up -d` и откройте `http://<адрес сервера>:8501`.
Docker публикует порты в обход правил ufw, поэтому на публичном сервере держите
`HSR_BIND=127.0.0.1` и ходите через nginx с паролем.

База данных лежит в томе `hsr-storage` (в контейнере — `/app/storage`). Она
содержит только состояние парка и результаты последнего прогона на вкладке
«Симуляция»; если её удалить, при запуске панель создаст парк заново.
Результаты отчётного года хранятся отдельно, в `data/last_run/`.

Повседневные команды:

```bash
docker compose logs -f dashboard             # журнал
git pull && docker compose up -d --build     # обновление
docker compose down                          # остановка (том сохраняется)
docker compose down -v                       # остановка и удаление базы
```

## Вариант B. systemd без Docker

Пример для Debian 12 / Ubuntu 24.04. Системный Python на Ubuntu 22.04 (3.10)
не подходит.

```bash
sudo apt install -y python3 python3-venv git
sudo git clone <адрес репозитория> /opt/hsr
sudo python3 -m venv /opt/hsr/.venv
sudo /opt/hsr/.venv/bin/pip install -r /opt/hsr/requirements.txt

sudo useradd --system --no-create-home --shell /usr/sbin/nologin hsr
sudo cp /opt/hsr/deploy/hsr-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hsr-dashboard

systemctl status hsr-dashboard
curl -fsS http://127.0.0.1:8501/_stcore/health    # через несколько секунд: ok
```

Юнит запускает Streamlit на `127.0.0.1:8501` от пользователя `hsr`. Для сервиса
файловая система доступна только на чтение, кроме `/var/lib/hsr`, где лежит
база (`/var/lib/hsr/hsr.db`).

```bash
journalctl -u hsr-dashboard -f               # журнал
cd /opt/hsr && sudo git pull \
  && sudo /opt/hsr/.venv/bin/pip install -r requirements.txt \
  && sudo systemctl restart hsr-dashboard    # обновление
```

## nginx и HTTPS

Streamlit общается с браузером по WebSocket, поэтому прокси должен передавать
заголовки `Upgrade` и `Connection`. Готовая конфигурация лежит в
`deploy/nginx.conf.example` (команды ниже выполняются из корня репозитория).

```bash
sudo apt install -y nginx
sudo cp deploy/nginx.conf.example /etc/nginx/conf.d/hsr.conf
sudoedit /etc/nginx/conf.d/hsr.conf          # server_name — свой домен
sudo nginx -t && sudo systemctl reload nginx
```

HTTPS (Let's Encrypt): certbot сам допишет TLS и редирект с 80 на 443.

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d hsr.example.com
```

Доступ по паролю:

```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/hsr.htpasswd operator
```

Затем в `/etc/nginx/conf.d/hsr.conf` раскомментируйте две строки `auth_basic`
и перечитайте конфигурацию (`sudo systemctl reload nginx`). Порт 8501 наружу
открывать не нужно.

Две детали конфигурации важны:

- `proxy_set_header Host $http_host` — Streamlit принимает WebSocket, только
  если `Origin` совпадает с `Host` вместе с портом. Вариант `$host` отбрасывает
  порт и даёт `403` при доступе на нестандартном порту.
- `proxy_read_timeout 3600s` — долгие прогоны не должны обрываться по таймауту
  прокси.

Проверка, что WebSocket проходит через прокси (curl завершится по таймауту, это
нормально — важна первая строка ответа):

```bash
curl -si -N -m 5 https://hsr.example.com/_stcore/stream \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' | head -1
# ожидается: HTTP/1.1 101 Switching Protocols
```

## Настройки

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `HSR_DB_URL` | адрес базы (SQLAlchemy) для панели и `main.py` | `sqlite:///<корень проекта>/data/hsr.db`; в Docker — `sqlite:////app/storage/hsr.db`, в systemd — `/var/lib/hsr/hsr.db` |
| `HSR_BIND`, `HSR_PORT` | интерфейс и порт на хосте (только docker-compose, `.env`) | `127.0.0.1`, `8501` |
| `STREAMLIT_*` | любая опция Streamlit, например `STREAMLIT_SERVER_PORT` | см. `.streamlit/config.toml` |

В `.streamlit/config.toml` включены защита CORS и XSRF, отключены автоперезапуск
по изменению файлов, а в браузере показывается только тип исключения, без
трассировки и текста. Для отладки на сервере можно временно вернуть полные
сообщения: `STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=full`.

Дополнительная защита от подмены `Host` (DNS rebinding): переменная
`STREAMLIT_SERVER_ALLOWED_HOSTS=hsr.example.com` разрешает WebSocket только
для этого имени. Для нескольких имён используйте `server.allowedHosts` в
`.streamlit/config.toml`: из переменной окружения Streamlit принимает одно
значение.

## Если что-то не работает

| Симптом | Причина и решение |
|---|---|
| Страница бесконечно показывает «Please wait» или «Connecting» | WebSocket не доходит до Streamlit. Проверьте заголовки `Upgrade`/`Connection` и `Host` в nginx. В журнале Streamlit будет строка `Rejecting WebSocket connection with disallowed Origin or Host header`. |
| `502 Bad Gateway` | Панель не запущена: `docker compose ps` или `systemctl status hsr-dashboard`. |
| `ModuleNotFoundError: No module named 'src'` | Запуск не из корня проекта. Используйте `python -m streamlit run src/dashboard/app.py` из корня или задайте `PYTHONPATH`. |
| `database is locked` | Два прогона одновременно. Дождитесь окончания одного и повторите. |
| `unable to open database file` / `readonly database` | Процесс не может писать в каталог базы. Если вместо тома смонтирован каталог хоста, его владельцем должен быть uid 10001. |
| Контейнер `unhealthy` | `docker compose logs dashboard`. В первые 30 секунд после старта неудачные проверки не засчитываются. |

## Ресурсы

Замеры на 2 vCPU, Python 3.11, сценарий «нормальная эксплуатация» с планированием.
Один прогон занимает одно ядро.

| Что | Результат |
|---|---|
| Процесс Streamlit без нагрузки | около 250 МБ памяти |
| Прогон на 1 год через вкладку «Симуляция» | около 75 секунд |
| Прогон на 10 лет через вкладку «Симуляция» | 13–16 минут, пик памяти около 1 ГБ |
| Образ Docker | порядка 0,8 ГБ (оценка по размеру зависимостей, образ не собирался) |

На вкладке «Симуляция» по умолчанию выбран горизонт 10 лет: первый же клик по
«Запустить симуляцию» займёт ядро на 13–16 минут. Для одного прогона
хватает 2 ГБ памяти, для двух одновременных нужно 4 ГБ.
