# Утилита перехода в fastboot

`enter-fastboot` предназначена только для работающего 64-битного Linux на
плате `thwc,ufi001c`. Без аргументов и с `--check` она только проверяет модель.
С `--reboot` она синхронизирует данные, переводит корневую файловую систему
в режим чтения и вызывает Linux RESTART2 с параметром `bootloader`.
Образы прошивки и таблицу разделов утилита не записывает.
Фактический переход на физическом модеме ещё не проверен.

Подготовка в Windows из папки готовой сборки, при подключении к Wi-Fi модема:

```bat
scp -O enter-fastboot root@192.168.1.1:/tmp/enter-fastboot
ssh root@192.168.1.1
```

Пароль root сначала задаётся в LuCI текущей прошивки. Затем в SSH:

```sh
chmod 700 /tmp/enter-fastboot
/tmp/enter-fastboot --check
```

После ответа `UFI001C detected. Check only; no changes made.` и подготовки
файлов прошивки переход в fastboot выполняется отдельно:

```sh
/etc/init.d/modemmanager stop
/etc/init.d/rmtfs stop
/tmp/enter-fastboot --reboot
```

Wi-Fi и SSH отключатся. Если утилита сообщит ошибку, сохраните её текст;
к записи образов не переходите. Службы можно включить снова командами
`/etc/init.d/rmtfs start` и `/etc/init.d/modemmanager start`.

На Windows устройство проверяется прежним fastboot.exe:

```bat
"%USERPROFILE%\uni-flash\fastboot.exe" devices
```

Запись boot.img/rootfs выполняется отдельным шагом после проверки образов.
Для этого устройства ранее отображался серийный номер b06ae7.
