# Загрузка пакетов и DNS через прокси · 3.1.4

| Обращение | Путь |
|---|---|
| LuCI → Программное обеспечение | apk → закрытый wget-адаптер → curl CONNECT → локальный Podkop 127.0.0.1:4534 → выбранный прокси |
| Обычные apk update / apk add | Тот же путь |
| Списки Podkop и проверка новой версии | Через прокси |
| Основной защищённый DNS | DoH через прокси-секцию |
| Bootstrap DNS | Прямое разрешение имени самого прокси, чтобы избежать DNS-петли |
| SSH, LuCI, локальный DHCP | Локальный доступ |

До ввода личной рабочей ссылки туннеля нет. Сохраните ссылку в Службы → Podkop,
запустите службу, затем проверьте `ufi-services-check --apk-update`.
Ожидаются GitHub/OpenWrt via service proxy: PASS и APK index update: PASS.
После этого проверьте меню пакетов и одну установку. Локальный listener не открыт
в LAN. curl передаёт HTTPS-имя через CONNECT, поэтому прямой DNS к GitHub не нужен.

При отказе службы нет автоматического прямого скачивания. Проверка конфигурации
отклоняет прямые выходы, пустые/циклические группы и цепочки detour. Selector и
URLTest поддерживаются, включая только прокси в fallback. Сертификаты и подписи
APK не отключаются. Ошибка загрузки списка не уничтожает предыдущий файл.

Настройка `podkop.settings.ufi_service_proxy` равна 1 по умолчанию. Явное отключение
через интерфейс разрешит прямые служебные загрузки. Это не гарантирует, что оператор
их пропустит. Глобальный http_proxy не используется: он затрагивал бы локальные
проверки и не гарантировал работу HTTPS у встроенного wget.

Используйте команду `apk`, а не абсолютный `/usr/bin/apk`: последний обходит обёртку.
Произвольные новые программы и сторонние загрузчики автоматически не перенастраиваются.
Индивидуальные настройки прокси не включены в публичный образ.

Не выполняйте массовое apk upgrade. Для kmod нужен точный ABI ядра этой сборки;
несовместимость модуля — отдельная ошибка. Live-патч меняет файлы, но не базу APK;
целостную установленную версию пакетов обеспечивает новый полный образ.

Проверено по закреплённым исходникам:

- [APK в OpenWrt](https://github.com/openwrt/openwrt/blob/f0a60eee2fe051741c643ea6118718aae1ef17fb/package/system/apk/Makefile): backend wget.
- [Аргументы wget в APK](https://github.com/alpinelinux/apk-tools/blob/b5a31c0d865342ad80be10d68f1bb3d3ad9b0866/src/io_url_wget.c).
- [LuCI package-manager-call](https://github.com/openwrt/luci/blob/128a7812f4be233c5dd7f7466f534fd888785caf/applications/luci-app-package-manager/root/usr/libexec/package-manager-call).
- [Podkop 0.7.24, 11 октября 2026](https://github.com/itdoginfo/podkop/releases/tag/0.7.24): исправление FakeIP для составных списков.

Полная сборка после prepare.py / feeds install требует finalize_feeds.py:
он сверяет хеш и устанавливает PATH в LuCI helper. check_config.py проверяет
включение ufi-service-proxy; check_rootfs.py сверяет файлы в готовом rootfs.
