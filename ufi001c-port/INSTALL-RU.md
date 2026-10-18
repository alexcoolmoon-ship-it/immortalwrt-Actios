# v3.2.0: единый USB-комплект

Стоковый модем: [короткая инструкция](migration/README-STOCK-RU.md), полный USB-ZIP,
INSTALL.cmd. Python и EDL включены. Новый Windows-ПК: DRIVER-HELP.cmd при необходимости.
Для уже установленного OpenWrt используйте процедуру ниже; конвертация со стока не подходит.

# UFI001C — установка v3.2.0 Final Candidate

OpenWrt 25.12.5, Podkop 0.7.24, XHTTP r12. Статус: кандидат, аппаратная приёмка
ещё нужна. Один готовый USB-ZIP появится после успешной сборки. Архив исходников
нельзя загружать в модем как прошивку. Заводская веб-прошивка не подтверждена.

## 1. Обновить GitHub и собрать

1. Распакуйте `UFI001C-LTE-Fix-v3.2.0.zip`.
   Загрузите содержимое `github-update` с заменой в корень репозитория:
   https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/upload/main
   Саму папку `github-update` и архив ZIP загружать в корень не нужно.
2. **Отдельно обновите workflow**, иначе останется старая версия в Actions:
   https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/edit/main/.github/workflows/openwrt25-ufi001c.yml
   Замените весь текст содержимым файла `openwrt25-ufi001c.yml` с верхнего уровня
   комплекта. Это копия `.github/workflows/openwrt25-ufi001c.yml`.
   Каталог в репозитории называется строго `.github`, в нижнем регистре.
3. Сохраните коммит в main. Первая строка workflow должна быть:
   `name: OpenWrt 25.12.5 UFI001C v3.2.0 Final Candidate (USB installer rc1, CI r2)`.
4. Откройте Actions:
   https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/actions/workflows/openwrt25-ufi001c.yml
   **Run workflow → main**, публикация включена. Нужен новый запуск:
   Re-run старого задания использует старый коммит.
5. После Success откройте Releases:
   https://github.com/alexcoolmoon-ship-it/immortalwrt-Actios/releases
   Скачайте из Assets **UFI001C-OpenWrt-25.12.5-v3.2.0-USB.zip**.
   При выключенной публикации скачайте артефакт **UFI001C-v3.2.0-USB-Download**
   на странице успешного запуска и распакуйте внешний ZIP.

Сборка проверяет фактическую конфигурацию ядра, новые файлы в rootfs и ARM64-движок
в QEMU. Неуспешный запуск не считается готовой прошивкой. Source code — исходники.
Для отдельного нового репозитория в комплекте есть `source-full.zip`.

## 2. Обновить модем с уже установленным OpenWrt

Нужен подготовленный Windows-ПК с прежним Python/EDL. `INSTALL.cmd` для стока
здесь не применяется. Сначала сохраните свои настройки Podkop в CMD Windows:

```cmd
if not exist "%USERPROFILE%\UFI001C-backups" mkdir "%USERPROFILE%\UFI001C-backups"
scp -O root@192.168.1.1:/etc/config/podkop "%USERPROFILE%\UFI001C-backups\podkop-before-v3.2.0.conf"
```

Продолжайте после успешного копирования. Этот файл содержит личную прокси-ссылку;
он не заменяет полный бэкап и не предназначен для публикации.

Из готового USB-ZIP перенесите содержимое `UFI001C-USB` в
`%USERPROFILE%\openwrt25-v3.2.0`, чтобы там сразу лежали boot.img, system.img,
SHA256SUMS и ufi-install.py. Оставьте подключаемым один модем. Отключите его,
зажмите кнопку, подключите к USB и отпустите кнопку — режим EDL.

```cmd
cd /d "%USERPROFILE%\openwrt25-v3.2.0"
"%USERPROFILE%\edl\.venv\Scripts\python.exe" ufi-install.py backup --images "%USERPROFILE%\UFI001C-backups"
```

Нужно не меньше 10 GiB свободного места. Дождитесь BACKUP VERIFIED. Скопируйте
напечатанный полный путь к свежему emmc.bin в следующую команду:

```cmd
"%USERPROFILE%\edl\.venv\Scripts\python.exe" ufi-install.py update --backup "ПОЛНЫЙ_ПУТЬ_К_СВЕЖЕМУ_БЭКАПУ\emmc.bin"
```

Установщик проверяет образы, GPT, принадлежность бэкапа и запись чтением обратно.
Настройки и дополнительные приложения в rootfs заменятся. После UPDATE VERIFIED
переподключите USB без кнопки. Wi-Fi и пароль — в новом ACCESS.txt,
панель — http://192.168.1.1 . Установите пароль root.

Сначала проверьте LTE по разделу 4. Затем восстановите только Podkop:

```cmd
scp -O "%USERPROFILE%\UFI001C-backups\podkop-before-v3.2.0.conf" root@192.168.1.1:/tmp/podkop.saved
ssh root@192.168.1.1 "cp /tmp/podkop.saved /etc/config/podkop && chmod 600 /etc/config/podkop && /etc/init.d/podkop restart"
```

Старую конфигурацию всей сети поверх новой не переносите: она может убрать
профиль частот. Обновите LuCI с Ctrl+F5. В настройках DNS оставьте выбор прокси
«Автоматически». После успешных проверок включите автозапуск Podkop.

## 3. Прошить новый заводской модем

1. На подготовленном Windows-ПК распакуйте готовый USB-ZIP и запустите
   **UFI001C-USB/INSTALL.cmd**. Следуйте подсказкам входа в EDL.
2. Программа проверит точный заводской профиль, затем прочитает всю память
   блоками по 32 МБ в `%USERPROFILE%\UFI001C-backups`. До завершения этого этапа
   записи на модем нет. Подготовка ПК: [README-STOCK-RU.md](migration/README-STOCK-RU.md).
3. Если чтение оборвалось, сохраните ВСЮ напечатанную папку. Переподключите
   **тот же модем сразу в EDL**, не загружая сток. Запустите **RESUME.cmd**
   из нового комплекта, выберите `backup-state.json` из прерванного запуска.
   Он заново проверит устройство и сохранённые данные, затем продолжит чтение.
4. После полного бэкапа, проверки радио, NV и подготовки плана установщик
   предложит подтвердить запись словом `UFI001C`. Дождитесь проверки записи.
   Затем переподключите USB без кнопки и используйте новый ACCESS.txt.

Проверяются два конкретных профиля UFI001C 4/8 ГБ и радио UFI001BC 20211121.
Внешнего сходства недостаточно. Старый частичный emmc.bin от версии rc1 нельзя
превратить в проверенный бэкап переименованием: первый запуск нового установщика
читает заново. `emmc.partial` не подходит для RESTORE.cmd.

Если ошибка USB повторяется на одном секторе даже при новом блочном чтении,
установщик остановится. Нельзя обещать программное устранение неисправности eMMC
или питания. Сохраните небольшой `edl-failure.json` и `backup-state.json`.
Если запись уже начиналась, RESUME не используется: нужен RESTORE.cmd и полный
бэкап своего экземпляра. Условия восстановления — в README-STOCK-RU.md.

## 4. Приёмка после загрузки

Следующие команды выполняются **в SSH модема**, по одной строке:

```sh
cat /etc/ufi001c-release
uci get network.modem.allowedmode
uci get network.modem.allowedbands
uci -q get network.modem.excludedbands
mmcli -m any
ufi-lte-health status
ip -4 route get 77.88.8.8
logread | grep -E 'ufi-lte-health|rflte_|handling crash' | tail -20
```

Ожидается версия 3.1.6, `4g`, supported-lte с исключениями B7/B20
(или явная маска APPLY-LTE на обновлённой установке), connected/LTE; три значения
`link=1 address=1 default_route=1`. Маршрут к внешнему IP идёт через текущий
wwanN без Network unreachable. Это проверка локального IPv4, ещё не Интернета.

После сохранения рабочей прокси-ссылки:

```sh
sh /usr/lib/podkop/ufi_dns_tunnel.sh check
ufi-services-check --apk-update
```

Проверьте HTTPS-сайты с телефона/ПК, затем обновление списка пакетов в LuCI.
Повторите после переподключения питания и 30 минут работы: boot_id не меняется,
RF-crash не нарастает, восстановления LTE не повторяются постоянно.
Полезный отчёт без прокси-ссылки: `ufi-stability-report`.

Если LTE исправен, а DNS нет, отдельно проверяются доступность bootstrap DNS
и туннеля. Работающий процесс sing-box и выдача FakeIP не доказывают прохождение HTTPS.

## 5. Профиль LTE и восстановление доступа

Новая установка: только 4G, все заявленные LTE-диапазоны, кроме **B7/B20**.
На наблюдавшемся экземпляре именно эти два выбора дважды вызвали RF-assert.
Не зарегистрировавшиеся B5/B8/B38–41 сохранены. Это не ремонт закрытой
радиопрошивки и не сертификация её других сочетаний с NV.
[Результаты и область применимости](LTE-BANDS-RU.md).

Для существующего модема сначала используйте `live-fix/APPLY.cmd` из исходников
или `APPLY-LTE/APPLY.cmd` из комплекта исправления. Он совместим с протоколом
установленной v3.1.5, сохраняет backup, ставит явную маску и имеет резерв B1/B3.
Простая запись supported-lte в старый netifd не поддерживается.

Отключить только автоматическое восстановление без перезапуска сети:

```sh
uci set ufi-lte-health.main.enabled='0'
uci commit ufi-lte-health
/etc/init.d/ufi-lte-health stop
```

Настройки Wi-Fi/AP и кнопки ufi-care сохранены. AP+STA одновременно не заявляется.
Не переключайте единственный Wi-Fi в клиент без проверенного доступа USB Ethernet.
XHTTP уже встроен. Zapret установлен, но стратегию и пересечение правил с Podkop
нужно проверить отдельно. Не применяйте массовое apk upgrade к этой сборке.
