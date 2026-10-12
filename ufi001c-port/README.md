# UFI001C OpenWrt 25.12.5 · v3.1.4 Services

[Страница проекта](../README.md) · [Установка](INSTALL-RU.md) ·
[Изменения](RELEASE-NOTES-v3.1.4.md) · [Проверки](VALIDATION-v3.1.4.md) ·
[Пакеты и прокси](SERVICES-RU.md) · [Стабильность USB](STABILITY-RU.md)

Исходники персональной сборки для UFI001C. Workflow
`.github/workflows/openwrt25-ufi001c.yml` собирает и проверяет образ, затем
публикует единый USB-ZIP как предварительный выпуск. Включены Podkop 0.7.24,
podkop-engine 1.13.21-r12 с XHTTP, настройки DNS и пакет ufi-service-proxy.
Для установленной 3.1.2/3.1.3 предусмотрен services-hotfix без записи образов.
Аппаратная стабильность нового образа ещё требует проверки.
