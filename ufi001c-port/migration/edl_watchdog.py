"""Bounded EDL subprocess supervision. No USB commands/retries of its own.

Reads fail on a USB exception or on an output file that stops growing. Writes
retain their original total deadline; they are never automatically retried.
The caller still owns all size/hash/profile checks and write readback.
"""
import json
from pathlib import Path
import subprocess
import threading
import time


class EDLError(ValueError):
    pass


class OutputCapture:
    def __init__(self, stream, log, limit):
        self.stream, self.log, self.limit = stream, log, limit
        self.saved = 0
        self.tail = b''
        self.usb_error = ''
        self.failure = ''
        self.truncated = False

    def run(self):
        try:
            while True:
                chunk = self.stream.read(65536)
                if not chunk:
                    break
                combined = self.tail + chunk
                if not self.usb_error:
                    for token in (b'USBError(', b'USB Overflow', b'LIBUSB_ERROR_'):
                        pos = combined.find(token)
                        if pos >= 0:
                            self.usb_error = combined[pos:pos+256].split(b'\n', 1)[0].decode('utf-8', 'replace')
                            break
                self.tail = combined[-8192:]
                take = min(len(chunk), max(0, self.limit-self.saved))
                if take:
                    self.log.write(chunk[:take])
                    self.log.flush()
                    self.saved += take
                if take < len(chunk) and not self.truncated:
                    self.log.write(b'\n[EDL output limit reached; remaining output drained, not saved]\n')
                    self.log.flush()
                    self.truncated = True
        except Exception as exc:
            self.failure = str(exc)


def file_size(path):
    try:
        return Path(path).stat().st_size
    except FileNotFoundError:
        return 0


def run_edl(command, cwd, action, args, log_path, *, idle_timeout=180,
            total_timeout=45*60, poll_interval=0.25, log_limit=4*1024**2,
            status_interval=30):
    """Run one existing EDL command. Never reset/reconnect/retry the device."""
    log_path = Path(log_path)
    reading = action in ('rf', 'rs')
    destination = Path(args[-1]) if reading else None
    started = last_progress = last_status = time.monotonic()
    previous_size = file_size(destination) if reading else None
    capture = None
    reason = None
    try:
        with log_path.open('ab') as log:
            log.write(('\nEDL: ' + action + '\n').encode()); log.flush()
            proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=0)
            capture = OutputCapture(proc.stdout, log, log_limit)
            reader = threading.Thread(target=capture.run, daemon=True)
            reader.start()
            try:
                while True:
                    code = proc.poll()
                    if code is not None:
                        reader.join(timeout=5)
                        if reader.is_alive():
                            reason = 'output-drain-timeout'
                            raise EDLError('EDL: журнал не завершён; проверьте edl-failure.json.')
                        if capture.failure:
                            reason = 'log-capture-error'
                            raise EDLError('EDL: ошибка сохранения журнала: ' + capture.failure)
                        if capture.usb_error:
                            reason = 'usb-error'
                            raise EDLError('EDL: USB-сбой; операция не подтверждена. См. edl-failure.json.')
                        if code:
                            reason = 'process-exit'
                            raise subprocess.CalledProcessError(code, command)
                        return
                    if capture.failure:
                        reason = 'log-capture-error'
                        raise EDLError('EDL: ошибка сохранения журнала: ' + capture.failure)
                    if reading and capture.usb_error:
                        reason = 'usb-error'
                        raise EDLError('EDL: USB-сбой при чтении; чтение остановлено. См. edl-failure.json.')
                    now = time.monotonic()
                    size = file_size(destination) if reading else None
                    if reading and size > previous_size:
                        last_progress = now
                        previous_size = size
                    if reading and now-last_progress >= idle_timeout:
                        reason = 'read-stalled'
                        raise EDLError(f'EDL: файл чтения не растёт {idle_timeout:g} с; операция остановлена. См. edl-failure.json.')
                    if now-started >= total_timeout:
                        reason = 'total-timeout'
                        raise EDLError('EDL: превышено общее время операции. См. edl-failure.json.')
                    if now-last_status >= status_interval:
                        elapsed = int(now-started)
                        suffix = f', считано {size//1048576} МБ' if reading else ''
                        print(f'  EDL {action}: {elapsed//60} мин {elapsed%60} с{suffix}; журнал: {log_path}', flush=True)
                        last_status = now
                    # Short waits keep cancellation and USB error handling responsive.
                    try:
                        proc.wait(timeout=poll_interval)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
                reader.join(timeout=5)
                proc.stdout.close()
    except BaseException as exc:
        report = {
            'format': 1, 'action': action, 'reason': reason or type(exc).__name__,
            'elapsed_seconds': round(time.monotonic()-started, 2),
            'output_bytes': file_size(destination) if reading else None,
            'output_file': str(destination) if reading else None,
            'usb_error': capture.usb_error if capture else '',
            'log_truncated': capture.truncated if capture else False,
            'output_tail': capture.tail.decode('utf-8', 'replace') if capture else '',
            'operation_confirmed': False,
            'note': 'Partial reads are not verified backups. No retry/reset or subsequent command was issued by this supervisor.'
        }
        # Never hide the original USB/process error if the host disk is full.
        try:
            path = log_path.with_name('edl-failure.json')
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
            tmp.replace(path)
        except OSError:
            pass
        raise
