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


DABING_HOTOVO = r"S:\DABING\hotovo"
POSLEDNI = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Recent")


KANAL = "UCNTZ-KNhpz9TZDSQSRlyhPQ"  # ULTRA SAMI
YT_KAZDYCH_S = 300
_YT = {"t": 0.0, "data": {}}


def _yt_get(u):
    import urllib.request
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
         "Accept-Language": "cs,en;q=0.8", "Cookie": "SOCS=CAI; CONSENT=YES+1"}
    return urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=20).read().decode("utf-8", "replace")


def _stopy(vid):
    """Zveřejněné zvukové stopy videa z jeho stránky (veřejné i NEVEŘEJNÉ video; soukromé stránku nemá)."""
    import re
    html = _yt_get(f"https://www.youtube.com/watch?v={vid}")
    return sorted({m.split(".")[0].split("-")[0].lower() for m in re.findall(r'"audioTrack":\{"displayName":"[^"]+","id":"([^"]+)"', html)})


def yt_zverejneno(dabing_ts):
    """Video k dabingu a jeho zveřejněné zvukové stopy – bez API a kvóty:
    1) nejnovější video, které web našel přes propojení YouTube (jen čtení) – i NEVEŘEJNÉ (vyroba_dabyt_video.json),
    2) jinak nejnovější VEŘEJNÉ video z RSS kanálu.
    Nahrané do Studia smí být nejvýš 48 h před dabingem; veřejné z RSS zveřejněné nejvýš 6 h před ním (jinak by se
    nový dabing spároval se včerejším videem). → {"id", "nazev", "jazyky": [..], "stav"} nebo {}"""
    import datetime as _dt
    import re
    try:
        v = json.load(open(os.path.join(vyroba_stav.SLOZKA, "vyroba_dabyt_video.json"), encoding="utf-8"))
        if v.get("id") and (v.get("nahrano") or 0) / 1000 >= dabing_ts - 48 * 3600:
            return {"id": v["id"], "nazev": v.get("nazev", ""), "stav": v.get("stav", ""), "jazyky": _stopy(v["id"]) if v.get("stav") != "private" else []}
    except Exception:  # noqa: BLE001
        pass
    rss = _yt_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={KANAL}")
    for vid, title, pub in re.findall(r"<yt:videoId>([^<]+)</yt:videoId>.*?<title>([^<]+)</title>.*?<published>([^<]+)</published>", rss, re.S):
        if _dt.datetime.fromisoformat(pub).timestamp() < dabing_ts - 6 * 3600:
            break  # RSS je od nejnovějšího – starší videa už k tomuhle dabingu nepatří
        return {"id": vid, "nazev": title, "stav": "public", "jazyky": _stopy(vid)}
    return {}


def dabing_yt(minule):
    """📺 Jazyky dabingu nejnovějšího videa v YouTube Studiu – všechno samo:
    • nahrává se = soubor „<video>_<jazyk>.mp3/mp4“ vybraný v okně „Otevřít“ (tak ho nahráváš do Studia) – Windows ho
      zapíše do Posledních položek („<video>_<jazyk>.lnk“);
    • publikováno = jazyk je u videa na YouTube zveřejněný (co 5 min, dokud nejsou všechny).
    Vrací data, nebo None beze změny."""
    import re
    try:
        slozky = [e for e in os.scandir(DABING_HOTOVO) if e.is_dir()]
        if not slozky:
            return None
        sl = max(slozky, key=lambda e: e.stat().st_mtime)
        video = sl.name
        jazyky = {}
        for e in os.scandir(sl.path):
            m = re.match(re.escape(video) + r"_([a-z]{2,3})\.(mp3|mp4|m4a|wav)$", e.name, re.I)
            if m:
                k = m.group(1).lower()
                jazyky[k] = max(jazyky.get(k, 0), e.stat().st_mtime)
        nahrano = {}
        for k, t in jazyky.items():
            lnk = os.path.join(POSLEDNI, f"{video}_{k}.lnk")
            if os.path.exists(lnk) and os.path.getmtime(lnk) >= t - 5:
                nahrano[k] = int(os.path.getmtime(lnk) * 1000)
        yt = _YT["data"] if _YT.get("video") == video else {}
        hotovo_vse = yt and all(k in yt.get("jazyky", []) for k in jazyky)
        if jazyky and not hotovo_vse and time.time() - _YT["t"] >= YT_KAZDYCH_S:
            _YT["t"] = time.time()
            try:
                yt = yt_zverejneno(min(jazyky.values()))
                _YT.update(video=video, data=yt)
            except Exception:  # noqa: BLE001 – bez internetu / YouTube nedostupný: zkusí se příště
                pass
        data = {"video": video, "jazyky": sorted(jazyky), "nahrano": nahrano,
                "publikovano": [k for k in sorted(jazyky) if k in (yt.get("jazyky") or [])],
                "yt": {"id": yt["id"], "nazev": yt["nazev"]} if yt.get("id") else None}
        return None if data == minule else data
    except Exception:  # noqa: BLE001
        return None


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
    dyt, dyt_kontrola = None, 0.0
    # soubor pro ID nejnovějšího videa (zapisuje ho web po propojení YouTube jen pro čtení) musí založit tenhle počítač –
    # účet vlastníka složky; mobil na Disku zakládat nemůže, jen přepsat
    try:
        if os.path.isdir(vyroba_stav.SLOZKA) and not os.path.exists(os.path.join(vyroba_stav.SLOZKA, "vyroba_dabyt_video.json")):
            vyroba_stav.uloz("dabyt_video", {})
    except Exception:  # noqa: BLE001
        pass
    while True:
        try:
            if time.time() - dyt_kontrola >= 20:  # 📺 dabing nahraný do YouTube Studia
                dyt_kontrola = time.time()
                nove = dabing_yt(dyt)
                if nove is not None:
                    dyt = nove
                    vyroba_stav.uloz("dabyt", dict(nove, t=int(time.time() * 1000)))
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
