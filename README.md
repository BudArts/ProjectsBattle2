# ВСМ · Система управления эксплуатационной готовностью парка

Решение для конкурса проектных работ: трёхуровневое планирование ТО парка из
43 высокоскоростных поездов при цели готовности **≥ 89% (≥ 38 составов)**,
с цифровым двойником (SimPy), исполняющим планы, и веб-интерфейсом на
Streamlit в стиле ОАО «РЖД».

## Установка

Нужен Python 3.11 (на других версиях проект не проверялся).

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Запуск

```bash
python main.py                 # веб-интерфейс (Streamlit, порт 8501)
python main.py web --host 127.0.0.1 --port 8080           # другой адрес и порт
python main.py sim --years 2 --seed 7 --scenario normal   # прогон из CLI
python main.py sim --years 1 --no-planning                # базовый (реактивный) режим
python main.py seed            # пересоздать парк в БД
```

База данных — SQLite, по умолчанию `data/hsr.db` в корне проекта. Другой адрес
задаётся переменной окружения `HSR_DB_URL`.

## Тесты

```bash
pip install -r requirements-dev.txt
python -m pytest -q            # юнит- и интеграционные тесты (около полутора минут)
python test_run.py             # сквозной демо-прогон (1 год) + экспорт Excel
```

## Развёртывание на сервере

```bash
cp .env.example .env
docker compose up -d --build   # панель на http://127.0.0.1:8501
```

Docker Compose, systemd, nginx, HTTPS, доступ по паролю и ограничения —
в [docs/DEPLOY.md](docs/DEPLOY.md).

## Структура

```
src/core/        конфигурация, модель данных (SQLite), сущности, суточный оборот
src/planning/    strategic (PuLP, 12 мес) · tactical (OR-Tools CP-SAT, 14 дн) · operational (правила)
src/simulation/  цифровой двойник (SimPy) — исполняет планы, собирает метрики и ML-выборку
src/dashboard/   веб-интерфейс (Streamlit): KPI, аналитика, депо, парк, движение, ML
data/            last_run/ — сохранённый прогон, который показывает панель
deploy/          пример конфигурации nginx и unit-файл systemd
docs/            описание продукта (PRODUCT.md), развёртывание (DEPLOY.md)
```

## Документы

- [docs/PRODUCT.md](docs/PRODUCT.md) — описание того, как работает продукт.
- [docs/DEPLOY.md](docs/DEPLOY.md) — развёртывание на сервере.
