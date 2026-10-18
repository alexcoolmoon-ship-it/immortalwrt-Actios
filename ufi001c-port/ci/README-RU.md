# Сборка v3.1.5: исправления CI r2

Основа: публичный коммит `342155c5eb23f9659af7ab6d511dfb1f9f673c71`.
Правки подготовлены 17 октября 2026 года. Версия прошивки остаётся v3.1.5:
это исправление процесса сборки, а не новый набор радио или сетевых патчей.

## Что установлено и изменено

1. Рабочий конфиг — `ufi001c-port/overlay/ufi001c.config`, который `prepare.py`
   копирует в подготовленный OpenWrt. Корневой `config/ufi001c.config` используется
   другим сборщиком. В overlay уже было `CONFIG_CCACHE=y`, однако без
   `CONFIG_DEVEL=y` Kconfig удалял эту настройку при `make defconfig`.
   Добавлены DEVEL и пустой CCACHE_DIR; `check_config.py` теперь проверяет
   итоговые значения. Изменение проверено настоящим `make defconfig`.
2. Кэши `dl` и `.ccache` разделены. Ключи не содержат run_id, зависят от исходников,
   конфигурации и закреплённых версий. При изменении входных файлов создаётся новый
   ключ; fallback позволяет использовать совместимый предыдущий кэш. Записи
   GitHub Cache неизменяемы: один вечный ключ также был бы неправильным решением.
   Старый restore-keys мог восстанавливать кэш даже без точного попадания;
   отсутствие exact hit само по себе не означало отсутствие восстановления.
   Лимит ccache — 1500M. Статистика сохраняется в build-log/ccache-stats.txt
   и может включать счётчики из восстановленного кэша. Первый новый запуск
   наполняет новый кэш; реальное ускорение измеряется на следующей сборке.
3. `concurrency: ufi001c-firmware`, `cancel-in-progress: false`: новые запуски
   этой версии workflow не работают параллельно. Это не дедупликация: второй
   ручной запуск может дождаться первого и тоже собрать выпуск. Уже запущенный
   старый workflow без этой настройки в группу задним числом не попадёт.
4. Удалены устаревшие корневые `openwrt25-ufi001c.yml`, `Makefile`,
   `Makefile (2)`, `Makefile (3)`, `README (1).md` и активный отдельный
   `.github/workflows/ufi001c-usb-installer.yml`. Новые версии пакетных Makefile
   остаются на своих местах внутри overlay. Workflow других моделей сохранены.
5. Новый `ci/download_sources.py` отдельно выполняет этапы tools, toolchain,
   package и target с `IGNORE_ERRORS=`. Сбой любого этапа или терминальная
   ошибка make в его журнале останавливает сборку до Compile firmware.
   Это устраняет потерю статуса предыдущего submake в агрегатном `make download`.
   Обычный HTTP 404 первого зеркала не считается провалом, если проверенный
   альтернативный источник успешно скачан. SHA256 проверяет штатный механизм
   OpenWrt. В build-log остаются download.log и download-check.json.

## Проверить следующий запуск

- Имя workflow содержит `CI r2`.
- Prepare проходит проверку DEVEL/CCACHE после defconfig.
- Download sources заканчивается `All selected source-download stages passed.`
- Report compiler cache statistics показывает статистику реального ccache.
- При повторе с теми же входными файлами ключи кэша не изменяются.
- При тестовом двойном запуске один workflow работает, другой ожидает.
- Все прежние проверки ядра, rootfs, ARM64 XHTTP и образов остаются включены.

Успешный #10 уже опубликован отдельно:
https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/releases/tag/ufi001c-v3.1.5-10-1
Он не содержит CI r2 и не меняется от этого патча. Полная компиляция CI r2
в этой сессии не выполнялась; правки не отправлены в удалённый репозиторий.

## Первичные источники

- Конфиг OpenWrt, раздел CCACHE: https://github.com/openwrt/openwrt/blob/f0a60eee2fe051741c643ea6118718aae1ef17fb/config/Config-devel.in
- Агрегатная цель download: https://github.com/openwrt/openwrt/blob/f0a60eee2fe051741c643ea6118718aae1ef17fb/include/toplevel.mk
- Кэш GitHub Actions: https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
- Concurrency: https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
