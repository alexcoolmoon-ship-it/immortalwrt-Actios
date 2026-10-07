# Состояние проекта — 7 октября 2026

UFI001C/MSM8916, 384 MiB RAM. Windows 11, Alexand. SSH/LuCI: 192.168.1.1.
OpenWrt 25.12.5 установлен и работает. Пользователь подтвердил рабочий LTE
после ufi-radio-fix.tar.gz. Старое радио UFI001CT 20211106 падало rflte_msm.c:820;
собственное UFI001BC 20211121 исправило проблему. Не откатывать радио и не
возвращаться к SIM/PIN2 без новых причин.

Подготовлены: ufi-care.tar.gz (кнопка и возврат AP без прошивки), проект v2
(Podkop/DoH, zapret, kernel-модули, рабочее радио), EDL backup/inspect/update.
make defconfig + check_config.py прошли. Полная компиляция v2 не запускалась.

Следующие действия:
1. Установить ufi-care, короткое нажатие, logread -e ufi-care.
2. Загрузить .github и ufi001c-port в корень существующего GitHub, новый Run
   workflow OpenWrt 25.12.5 UFI001C v2 на новом main.
3. После Success — свежий полный бэкап, обновление, проверки LTE/Wi-Fi/DoH.
4. Подобрать стратегию zapret и добавить собственный прокси в Podkop.
5. Отдельно завершить stock→OpenStick GPT/bootloader конвертер: сейчас не готов.

Нет прямого доступа к ПК/модему или GitHub пользователя. Не заявлять, что
прошивка собрана, кнопка испытана или приложения установлены на устройстве.
Точное сообщение несовместимости Podkop ещё не предоставлено.
wcn36xx не объявляет AP+STA: реализован возврат доступа, не AP+STA.
Кнопка возвращает сохранённые конфиги, не стирает ext4 и пароль root.
Zapret в новом образе установлен с отключённым перехватом до проверки стратегии.

Репозиторий: alexcoolmoon-ship-it/immortalwrt-Actios.
Ранее успешная сборка: 37422719236, head 26ab292, примерно 53 минуты.
Windows EDL: %USERPROFILE%\edl\.venv\Scripts\python.exe, edl.py 3.62.
Заводской бэкап был %USERPROFILE%\edl\backup\ufi001c_full.bin.
Собственные NV-бэкапы: %USERPROFILE%\uni-flash\nv-backup.
