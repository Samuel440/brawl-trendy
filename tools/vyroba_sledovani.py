"""⏱️ Sledování času v DaVinci Resolve po minutách – pro živý ukazatel v záložce 🛠️ Výroba (i na mobilu).

Každou minutu: je DaVinci Resolve okno v popředí a pracoval jsi (myš / klávesnice) za poslední 2 minuty?
→ +1 minuta k projektu, který máš v Resolve otevřený. Otevřený, ale zapomenutý Resolve se nepočítá.
Zapisuje S:\\EDIT\\_cache\\cas_log.json ({den: {projekt: minuty}}) a vyroba_live.json na G: (web ho čte).
Spouští SLEDOVÁNÍ ČASU.bat (může běžet pořád, sám nic nedělá, když Resolve neběží). Nic neposílá jinam.
"""
import ctypes
import datetime as dt
import json
import os
import sys
import time
from ctypes import wintypes

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import vyroba_stav  # noqa: E402

LOG = r"S:\EDIT\_cache\cas_log.json"
NECINNOST_S = 120

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def necinnost_s():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    user32.GetLastInputInfo(ctypes.byref(li))
    return (kernel32.GetTickCount() - li.dwTime) / 1000


def popredi_exe():
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf, n = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
    try:
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    finally:
        kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower()


_RES = None


def projekt():
    """Jméno projektu otevřeného v Resolve (skriptovací rozhraní), nebo None."""
    global _RES
    try:
        if _RES is None:
            sys.path.insert(0, r"S:\EDIT\tools")
            from resolve_conn import dvr  # noqa: E402
            _RES = dvr.scriptapp("Resolve")
        p = _RES.GetProjectManager().GetCurrentProject() if _RES else None
        return p.GetName() if p else None
    except Exception:  # noqa: BLE001
        _RES = None
        return None


def nacti():
    try:
        return json.load(open(LOG, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def uloz(data):
    try:
        tmp = LOG + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, LOG)
    except Exception:  # noqa: BLE001
        pass


def main():
    print("⏱️  Sledování času v DaVinci běží – nech okno otevřené (nebo ho minimalizuj).")
    data = nacti()
    posledni = {"projekt": None, "aktivni": False}
    while True:
        try:
            den = dt.date.today().isoformat()
            exe = popredi_exe()
            v_resolve = exe in ("resolve.exe",)
            nazev = projekt() if v_resolve or posledni["projekt"] is None else posledni["projekt"]
            aktivni = bool(v_resolve and nazev and necinnost_s() < NECINNOST_S)
            if aktivni:
                d = data.setdefault(den, {})
                d[nazev] = d.get(nazev, 0) + 1
                uloz(data)
            if nazev:
                posledni["projekt"] = nazev
            posledni["aktivni"] = aktivni
            celkem = sum(v.get(posledni["projekt"], 0) for v in data.values()) if posledni["projekt"] else 0
            vyroba_stav.uloz("live", {"t": int(time.time() * 1000), "projekt": posledni["projekt"], "aktivni": aktivni,
                                      "resolve": exe == "resolve.exe" or _RES is not None, "dnes": data.get(den, {}),
                                      "projekt_celkem": celkem})
        except Exception as e:  # noqa: BLE001
            print("chyba:", e)
        time.sleep(60)


if __name__ == "__main__":
    main()
