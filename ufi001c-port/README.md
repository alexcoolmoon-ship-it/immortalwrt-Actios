# OpenWrt 25.12.5 для UFI001C

Это отдельный порт официальных исходников OpenWrt 25.12.5 на UFI001C (MSM8916).
Он не является официальным образом OpenWrt для этой платы. Поддержка платы,
Wi-Fi и Qualcomm-модема перенесена из зафиксированных исходников lkiuyu,
на которых работала предыдущая прошивка устройства.

Подходит для UFI001C с уже установленной разметкой OpenStick:
`boot` — раздел 12 (64 МиБ), `rootfs` — раздел 14.
Для заводской Android-разметки или другого варианта UFI эти образы не предназначены.

## Состав

- База OpenWrt 25.12.5, а не ImmortalWrt SNAPSHOT.
- LuCI с русским переводом, SSH, WPA2 Wi-Fi.
- Драйвер WCN36xx и соответствующие UFI001C firmware/NV-файлы Wi-Fi.
- Qualcomm remoteproc, BAM-DMUX, RPMSG, QRTR, rmtfs и ModemManager.
- USB RNDIS через менеджер gadget; ADB не включён.
- `boot.img`: Android boot v0, ядро AArch64 и DTB именно UFI001C.
- `system.img`: Android sparse ext4, начальный размер файловой системы 1 ГиБ.

Мобильный интерфейс `modem` изначально выключен. После первого входа нужно
задать APN своего оператора и включить интерфейс. Проверка LTE требует самого
модема и SIM-карты.

## Сборка в GitHub Actions

1. Добавьте в корень своего репозитория обе папки из внешнего архива:
   `.github` и `ufi001c-port`. Файл workflow расположен в
   `.github/workflows/openwrt25-ufi001c.yml`. Подробные шаги — в START-OPENWRT25.txt.
2. Откройте **Actions → OpenWrt 25.12.5 UFI001C**.
3. Нажмите **Run workflow → Run workflow**.
4. После успешного завершения скачайте артефакт **UFI001C-OpenWrt-25.12.5**.
5. В нём будут `boot.img`, `system.img`, контрольные суммы, результат проверки
   структуры образов и `ACCESS.txt` с паролем Wi-Fi этой сборки.

Исходники и feeds закреплены за конкретными коммитами в `sources.lock.json`.
Пароль Wi-Fi создаётся заново для каждого чистого каталога сборки и не хранится
в исходниках проекта.

## Локальная сборка (Ubuntu 24.04)

Пакеты для среды сборки перечислены во внешнем `.github/workflows/openwrt25-ufi001c.yml`.
После их установки:

```sh
python3 prepare.py /path/to/empty-build-directory
cd /path/to/empty-build-directory
./scripts/feeds update -a
./scripts/feeds install -a
make defconfig
python3 /path/to/this-project/check_config.py .config
make -j8 download
make -j8 V=s
python3 /path/to/this-project/collect.py . ../UFI001C-OpenWrt-25.12.5
python3 /path/to/this-project/migration/build_helper.py . ../UFI001C-OpenWrt-25.12.5
```

## После записи

Wi-Fi: **OpenWrt-25-UFI001C**. Пароль находится в `ACCESS.txt` готовой сборки.
Панель: **http://192.168.1.1/**, пользователь **root**. Пароль администратора
нужно задать при первом входе; изначально он пустой.

Предусмотрена запись только `boot.img` в `boot` и `system.img` в `rootfs`
через fastboot при уже подходящей разметке. Обновление через LuCI/sysupgrade
для этого порта намеренно отключено: эти два файла имеют другой формат.
Разделы с загрузчиком, GPT, modemst1/modemst2, fsg и fsc переписывать не нужно.

Сохраните исходный полный дамп, резервную копию NV и файлы текущей работающей
прошивки. Они не входят в этот проект и при сборке не изменяются.

Для этого неофициального target нет готового репозитория дополнительных
модулей ядра OpenWrt. Такие модули нужно собирать вместе с этим ядром.
Обычные feeds `aarch64_generic` остаются доступными.

## Проверки и ограничения

`check_config.py` проверяет выбор платы, Wi-Fi, прошивок радиомодулей,
ModemManager и ext4 после обработки конфигурации.
`collect.py` проверяет Android-заголовок, архитектуру ядра, DTB UFI001C,
параметр корневого раздела, структуру sparse-образа и сигнатуру ext4.
Эти проверки не подтверждают загрузку и работу радио на физическом устройстве.
Фактический результат локальной сборки указывается отдельно в `VALIDATION.md`.

## Источники и лицензии

- https://github.com/openwrt/openwrt — тег v25.12.5.
- https://github.com/lkiuyu/immortalwrt — поддержка msm89xx/UFI001C.
- https://github.com/lkiuyu/openstick-feeds — Qualcomm firmware и службы модема.
- https://github.com/aosp-mirror/platform_system_core — mkbootimg Android 10,
  исходный файл с лицензией Apache-2.0 сохранён в `overlay/scripts`.

Точные ревизии и SHA-256 mkbootimg находятся в `sources.lock.json`.
Сохраняются лицензии исходных файлов; новые скрипты настройки — GPL-2.0-only.
Архив содержит изменения и инструкции получения полных закреплённых исходников.
