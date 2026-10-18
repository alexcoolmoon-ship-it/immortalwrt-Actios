# Собрать v3.2.0: ссылки и действия

Репозиторий: https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios

Workflow: https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/actions/workflows/openwrt25-ufi001c.yml

Выпуски: https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/releases

## Обновить исходники

В подготовленном пакете папка github-update содержит изменённые и новые файлы
с путями относительно корня репозитория. Обновите их в ветке main, включая
`.github/workflows/openwrt25-ufi001c.yml` и `.gitattributes`. Не создавайте лишний
вложенный каталог github-update внутри репозитория. Полный бинарный образ прошивки
изменением исходников не заменяется: его нужно скомпилировать.

Сначала просмотрите CHANGED-FILES.txt и PATCH.diff. Удалите устаревший отдельный
workflow из DELETE-FILES.txt: полный USB-ZIP теперь собирается основным workflow.

## Запустить

1. Откройте Workflow по ссылке выше. Заголовок должен содержать **v3.2.0 Final Candidate / Portable USB**.
2. Нажмите **Run workflow**, выберите обновлённую ветку **main**, оставьте
   **publish_release=true** и запустите один раз.
3. Сначала должен пройти **windows-tools**, затем **firmware**.
4. После Success в Releases → Assets появится
   **UFI001C-OpenWrt-25.12.5-v3.2.0-USB.zip** и его `.sha256`.
   Запасной путь: Actions → выполненный запуск → Artifacts → **UFI001C-v3.2.0-USB-Download**.
5. Распакуйте USB-ZIP целиком. Стоковый модем устанавливается через **INSTALL.cmd**.

GitHub Source code ZIP, пакет обновления исходников и build-log не являются
готовой прошивкой. Успешная компиляция обязательна: она создаёт boot.img/system.img
и проверенные Windows-программы. Время зависит от кэша и загрузок, заранее не фиксируется.

Рабочая ветка `codex/ufi001c-final-v3.2.0` также собирается при push, но такой запуск
только сохраняет артефакты. Для публикации Assets используется ручной Run workflow.
Публикация — Pre-release до аппаратного испытания нового образа.

## Зафиксированные исходники

- OpenWrt: https://github.com/openwrt/openwrt/tree/f0a60eee2fe051741c643ea6118718aae1ef17fb
- Дополнения и версии: `ufi001c-port/addons.lock.json`.
- EDL: https://github.com/bkerler/edl/tree/1cda1a616748d608667b2d13b73a28d3197ceb45
- Loaders: https://github.com/bkerler/Loaders/tree/6c416255056a178f54ccd7ee9b67d04a5bdfa8e6
- Хэши и выбранные loaders: `ufi001c-port/migration/windows-runtime.lock.json`.
- Загрузочные компоненты: `ufi001c-port/migration/BASE-SOURCES.md`.

Индивидуальные дампы eMMC, NV, SIM-идентификаторы и proxy URL в Git не добавляются.
