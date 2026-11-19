"""Visible scheduled entry point using the current user's Windows DPAPI credential."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


def read_credential(path):
    ns = {'p': 'http://schemas.microsoft.com/powershell/2004/04'}
    root = ET.parse(path).getroot()
    username = root.find('.//p:S[@N="UserName"]', ns)
    password = root.find('.//p:SS[@N="Password"]', ns)
    if username is None or password is None:
        raise ValueError('Invalid encrypted credential file.')
    encrypted = bytes.fromhex(password.text)

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

    buffer = (ctypes.c_byte * len(encrypted)).from_buffer_copy(encrypted)
    source = Blob(len(encrypted), buffer)
    destination = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                      1, ctypes.byref(destination)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        plaintext = ctypes.string_at(destination.data, destination.size).decode('utf-16-le')
    finally:
        ctypes.memset(destination.data, 0, destination.size)
        kernel32.LocalFree(destination.data)
    return username.text, plaintext


def main():
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    path = logs / f'scheduled_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log'
    with path.open('w', encoding='utf-8') as log:
        def report(message):
            print(message, file=log, flush=True)
            print(message, flush=True)

        report(f'Started {datetime.now().astimezone().isoformat()}')
        report(f'Log: {path}')
        environment = os.environ.copy()
        try:
            username, password = read_credential(ROOT / '.secrets' / 'pim_credential.xml')
            environment.update(PIM_USERNAME=username, PIM_PASSWORD=password, PYTHONIOENCODING='utf-8')
            password = None
            with subprocess.Popen([sys.executable, '-u', str(ROOT / 'main.py'),
                '--config', str(ROOT / 'config.json'), '--headed'], cwd=ROOT, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding='utf-8', errors='replace') as process:
                for line in process.stdout:
                    report(line.rstrip('\r\n'))
                code = process.wait()
        except Exception as exc:
            report(f'Runner error: {type(exc).__name__}: {exc}')
            code = 1
        finally:
            environment.pop('PIM_PASSWORD', None)
        report(f'Finished {datetime.now().astimezone().isoformat()}; exit code {code}')
    return code


if __name__ == '__main__':
    sys.exit(main())
