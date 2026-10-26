"""⏱️ Sledování času v DaVinci Resolve po minutách – pro živý ukazatel v záložce 🛠️ Výroba (i na mobilu).

Každých 5 s: je DaVinci Resolve okno v popředí a pracoval jsi (myš / klávesnice) za poslední 2 minuty?
→ +5 s k projektu, který máš v Resolve otevřený. Otevřený, ale zapomenutý Resolve se nepočítá.
Na web co minutu, přepnutí projektu do ~15 s.
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


KROK_S = 5        # tak často se kouká, co děláš (čas se přičítá po 5 s)
ZAPIS_S = 60      # vyroba_live.json běžně co minutu…
ZMENA_S = 15      # …ale přepnutí projektu hned (nejvýš co 15 s – Drive nesnese častější přepis)


PID = r"S:\EDIT\_cache\sledovani.pid"


def zastav():
    """ZASTAVIT SLEDOVÁNÍ.bat → ukončí běžící kopii (číslo procesu je v sledovani.pid)."""
    try:
        pid = int(open(PID).read().strip())
        h = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            kernel32.TerminateProcess(h, 0)
            kernel32.CloseHandle(h)
            print("Sledování času zastaveno.")
            return
    except Exception:  # noqa: BLE001
        pass
    print("Sledování neběželo.")


def main():
    # jen jedna kopie (autostart + ruční spuštění by jinak počítaly čas dvakrát)
    global _MUTEX
    _MUTEX = kernel32.CreateMutexW(None, False, "BrawlTrendySledovaniCasu")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print("Sledování už běží.")
        return
    try:
        open(PID, "w").write(str(os.getpid()))
    except Exception:  # noqa: BLE001
        pass
    print("⏱️  Sledování času v DaVinci běží (bez okna přes pythonw, zastavíš ZASTAVIT SLEDOVÁNÍ.bat).")
    data = nacti()
    posledni = {"projekt": None, "aktivni": False}
    zapsano, zapsany_projekt, ulozeno = 0.0, None, time.time()
    while True:
        try:
            den = dt.date.today().isoformat()
            exe = popredi_exe()
            v_resolve = exe in ("resolve.exe",)
            nazev = projekt() if v_resolve or posledni["projekt"] is None else posledni["projekt"]
            aktivni = bool(v_resolve and nazev and necinnost_s() < NECINNOST_S)
            if aktivni:
                d = data.setdefault(den, {})
                d[nazev] = round(d.get(nazev, 0) + KROK_S / 60, 2)
            if nazev:
                posledni["projekt"] = nazev
            posledni["aktivni"] = aktivni
            now = time.time()
            if now - ulozeno >= ZAPIS_S:
                uloz(data)
                ulozeno = now
            zmena = posledni["projekt"] != zapsany_projekt
            if now - zapsano >= ZAPIS_S or (zmena and now - zapsano >= ZMENA_S):
                celkem = sum(v.get(posledni["projekt"], 0) for v in data.values()) if posledni["projekt"] else 0
                vyroba_stav.uloz("live", {"t": int(now * 1000), "projekt": posledni["projekt"], "aktivni": aktivni,
                                          "resolve": exe == "resolve.exe" or _RES is not None, "dnes": data.get(den, {}),
                                          "projekt_celkem": round(celkem, 1)})
                zapsano, zapsany_projekt = now, posledni["projekt"]
        except Exception as e:  # noqa: BLE001
            print("chyba:", e)
        time.sleep(KROK_S)


if __name__ == "__main__":
    zastav() if "--stop" in sys.argv else main()
