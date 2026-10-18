"""Locate the offline Windows runtime; never install drivers or fetch code."""
from pathlib import Path
import os
import sys


def bundled_edl():
    if not getattr(sys, 'frozen', False):
        return None
    root = Path(sys.executable).resolve().parent / 'edl'
    if not (root / 'UFI-EDL.exe').is_file():
        raise ValueError('Неполный комплект: нет runtime/edl/UFI-EDL.exe. Распакуйте весь USB-ZIP.')
    return root


def usb_backend():
    import usb.backend.libusb1
    root = bundled_edl()
    if root is None:
        return usb.backend.libusb1.get_backend()
    dll = root / '_internal/edlclient/Windows/libusb-1.0.dll'
    if not dll.is_file():
        raise ValueError('Не найдена USB-библиотека. Распакуйте весь USB-ZIP.')
    return usb.backend.libusb1.get_backend(find_library=lambda name: str(dll))


def require_single_edl():
    """Read device descriptors only. Do not configure, claim or reset USB."""
    import usb.core
    backend = usb_backend()
    if backend is None:
        raise ValueError('USB-библиотека недоступна. Запустите CHECK-PC.cmd; драйвер: DRIVER-HELP.cmd.')
    try:
        devices = list(usb.core.find(find_all=True, idVendor=0x05C6, idProduct=0x9008, backend=backend))
    except usb.core.USBError as exc:
        raise ValueError('Нет доступа к EDL USB. Запустите DRIVER-HELP.cmd (WinUSB для 05C6:9008).') from exc
    if len(devices) != 1:
        if not devices:
            raise ValueError('EDL 05C6:9008 не найден. Подключите модем с кнопкой; при необходимости DRIVER-HELP.cmd.')
        raise ValueError('Найдено несколько EDL-модемов. Оставьте подключённым только один.')


def self_test():
    import ctypes
    import gzip
    import hashlib
    import json
    import struct
    import tkinter
    import zlib
    import stock_install
    root = bundled_edl()
    if root is None or usb_backend() is None:
        raise ValueError('Portable runtime/backend is incomplete')
    stock_install.check_base(stock_install.HERE / 'stock-base')
    tkinter.Tcl().eval('info patchlevel')
    print('PORTABLE_INSTALLER_SELF_TEST_OK (no USB commands performed)', flush=True)


def driver_help():
    root = Path(sys.executable).resolve().parent
    guide = root.parent / 'DRIVER-RU.txt'
    zadig = root / 'Drivers/zadig-2.8.exe'
    if not guide.is_file() or not zadig.is_file():
        raise ValueError('Распакуйте весь USB-ZIP: отсутствуют инструкция или программа драйвера.')
    os.startfile(str(guide))
    os.startfile(str(zadig))
    return 0
