"""⏱️ Sledování času v DaVinci Resolve po minutách – pro živý ukazatel v záložce 🛠️ Výroba (i na mobilu).

Každých 5 s: je DaVinci Resolve okno v popředí a pracoval jsi (myš / klávesnice) za poslední 2 minuty?
→ +5 s k projektu, který máš v Resolve otevřený (jméno z TITULKU okna – do Resolve se kvůli tomu nikdy neptá,
dřív ho tím zamrazilo). Otevřený, ale zapomenutý Resolve se nepočítá, obrazovka projektů taky ne.
Na web co minutu, přepnutí projektu do ~15 s.
Zapisuje S:\\EDIT\\_cache\\cas_log.json ({den: {projekt: minuty}}) a vyroba_live.json na G: (web ho čte).
Spouští SLEDOVÁNÍ ČASU.bat (může běžet pořád, sám nic nedělá, když Resolve neběží). Nic neposílá jinam.
"""
import ctypes
import datetime as dt
import json
import os
import sys
import threading
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


# ⚠ NIKDY se neptat Resolve přes skriptovací rozhraní (ani kvůli času, ani kvůli renderu): Resolve vyřizuje dotazy v hlavním vlákně a při
# načítání obrazovky projektů / otevírání projektu kvůli nim celý zamrzl („neodpovídá“, 1. 11. 2026).
# Jméno projektu se bere z TITULKU OKNA („DaVinci Resolve Studio - <projekt>“, na výběru projektů „Project Manager“)
# a běžící procesy přímo z Windows (bez tasklistu – žádná blikající okna).


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE


def procesy(*jmena):
    """{jméno.exe: [pid, …]} běžících procesů (jen ty hledané) – přímo z Windows, nic se nespouští."""
    hledam, out = {j.lower() for j in jmena}, {}
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            n = e.szExeFile.lower()
            if n in hledam:
                out.setdefault(n, []).append(e.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


def proces_bezi_s(pid):
    """Kolik sekund proces běží (Resolve prvních pár minut po startu nechat úplně na pokoji)."""
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return 0
    try:
        c, x, k, u = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x), ctypes.byref(k), ctypes.byref(u)):
            return 0
        start = ((c.dwHighDateTime << 32) | c.dwLowDateTime) / 1e7 - 11644473600
        return time.time() - start
    finally:
        kernel32.CloseHandle(h)


_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def resolve_titulky(pids):
    """[(titulek, viditelné)] oken procesu Resolve. (Hlavní okno kreslí přes OpenGL a Windows ho hlásí jako
    NEviditelné i když na něm pracuješ → brát i neviditelná.)"""
    pids, out = set(pids), []

    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True
    user32.EnumWindows(_ENUM(cb), 0)
    return out


PM = "Project Manager"


def resolve_stav():
    """→ (běží, sekund od startu, jméno projektu | None, je na obrazovce projektů, je vepředu)"""
    import re
    pids = procesy("Resolve.exe").get("resolve.exe", [])
    if not pids:
        return False, 0, None, False, False
    okna = resolve_titulky(pids)
    nazev = None
    for t, _ in okna:
        m = re.match(r"^DaVinci Resolve(?: Studio)?\s+-\s+(.+)$", t)
        if m:
            nazev = m.group(1).strip()
            break
    na_pm = any(t == PM and vid for t, vid in okna)  # výběr projektů je otevřený (přes hlavní okno)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return True, max(proces_bezi_s(p) for p in pids), None if na_pm else nazev, na_pm, pid.value in pids


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


STAT_S = 600
# 🎬 Render se pozná JEN podle souboru v S:\EXPORT, který právě roste – do Resolve se NIKDY nic neposílá.
# (1. 11. 2026: i jeden dotaz přes skriptovací rozhraní, když byl Resolve vzadu, ho zamrazil na ~10 s.)
RENDER_DIR = r"S:\EXPORT"
ROSTE_S = 12        # soubor změněný za posledních 12 s = render běží
HOTOVO_TICHO_S = 25  # 25 s beze změny = render dokončený


class Render:
    """🎬 Render v DaVinci → karta „RENDER“ ve Výrobě (velikost, jak dlouho běží; odhad konce podle minulých renderů)
    + upozornění na mobil po dokončení. Pozná ho podle souboru, který se v S:\\EXPORT zapisuje."""
    def __init__(self):
        self.web, self.soubor, self.zmena, self.velikost = None, None, 0.0, 0
        self.snimek = False

    def _konec(self, ok, zprava):
        if self.web:
            self.web.konec(ok, zprava)
            if ok:
                self.snimek = True  # nový snímek z právě dokončeného videa
        self.web, self.soubor = None, None

    def kontrola(self, resolve_bezi):
        now = time.time()
        if self.web:
            try:
                st = os.stat(self.soubor)
            except OSError:
                self._konec(False, "Render zrušený (soubor zmizel)")
                return
            if st.st_size != self.velikost:
                self.velikost, self.zmena = st.st_size, now
            trva = now - self.web.d["start"] / 1000
            if now - self.zmena >= HOTOVO_TICHO_S:
                self._konec(True, f"✔ {os.path.basename(self.soubor)} vyrenderováno za {trva / 60:.1f} min · {self.velikost / 1e9:.2f} GB")
            elif not resolve_bezi:
                self._konec(False, "DaVinci Resolve se zavřel během renderu")
            else:
                self.web.nastav(procenta=None, zbyva_s=None, faze=f"📦 zapsáno {self.velikost / 1e9:.2f} GB · {self.velikost / max(trva, 1) / 1e6:.1f} MB/s")
            return
        if not resolve_bezi or not os.path.isdir(RENDER_DIR):
            return
        try:
            rostou = [e for e in os.scandir(RENDER_DIR) if e.is_file() and e.name.lower().endswith((".mp4", ".mov", ".mxf"))
                      and now - e.stat().st_mtime < ROSTE_S]
        except OSError:
            return
        if rostou:
            e = max(rostou, key=lambda x: x.stat().st_mtime)
            self.soubor, self.velikost, self.zmena = e.path, e.stat().st_size, now
            self.web = vyroba_stav.Stav("render", e.name)
            zacatek = getattr(e.stat(), "st_birthtime", None) or e.stat().st_ctime
            if now - zacatek < 6 * 3600:
                self.web.d["start"] = int(zacatek * 1000)
            self.web.radek(f"▶ Render → {e.path}")
            self.web.nastav(procenta=None, faze="📦 zapisuje se…")


# 🖼️ náhodný snímek z posledního vyrenderovaného videa (S:\EXPORT) → malý obrázek u AUTO EDITU ve Výrobě.
# Jen jeden snímek (lehké), nízká priorita, a nikdy když OBS nahrává. Nový po každém renderu a co 30 min.
EXPORT = r"S:\EXPORT"
FFMPEG = r"S:\DABING\_pro_vyvojare\.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
SNIMEK_S = 1800


def posledni_snimek():
    import random
    import re
    import shutil
    import subprocess
    try:
        ff = shutil.which("ffmpeg") or FFMPEG
        if procesy("obs-ffmpeg-mux.exe") or not os.path.isdir(vyroba_stav.SLOZKA):
            return
        vids = [e for e in os.scandir(EXPORT) if e.is_file() and e.name.lower().endswith((".mp4", ".mov"))
                and e.stat().st_size > 1e6 and time.time() - e.stat().st_mtime > 60]
        if not vids:
            return
        # nejnovější soubor, který má obraz („… zvuk.mp4“ bývá jen zvuková stopa)
        for f in sorted(vids, key=lambda e: e.stat().st_mtime, reverse=True)[:6]:
            info = subprocess.run([ff, "-hide_banner", "-i", f.path], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=30, creationflags=0x08000000).stderr
            v = re.search(r"Stream #.*Video: (?!mjpeg|png).*?(\d+) kb/s", info)
            if v and int(v.group(1)) >= 1000:  # (černý obraz u zvukové verze má jen ~150 kb/s)
                break
        else:
            return
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
        delka = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 60
        t = round(random.uniform(0.08, 0.92) * delka, 1)
        tmp = os.path.join(vyroba_stav.SLOZKA, "~vyr_posledni.jpg")
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", f.path, "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "4", "-f", "image2", tmp], timeout=60,
                       creationflags=0x08000000 | 0x00004000)  # bez okna, BELOW_NORMAL
        if os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
            os.replace(tmp, os.path.join(vyroba_stav.SLOZKA, "vyroba_posledni.jpg"))
            vyroba_stav.uloz("posledni", {"t": int(time.time() * 1000), "soubor": f.name, "cas_s": t, "delka_s": round(delka)})
    except Exception as e:  # noqa: BLE001
        print("snímek:", e)


# ⚠ DABING / AUTO EDIT spadl bez hlášení (zavřené okno, pád Pythonu) → soubor na Disku přestane „žít“ → upozornit
_SPADLE = {}


def hlidej_pady():
    for prog in ("edit", "dabing"):
        try:
            d = json.load(open(os.path.join(vyroba_stav.SLOZKA, f"vyroba_{prog}.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        ticho = time.time() - (d.get("t") or 0) / 1000
        if d.get("stav") == "bezi" and 180 < ticho < 6 * 3600 and _SPADLE.get(prog) != d.get("start"):
            _SPADLE[prog] = d.get("start")
            vyroba_stav.upozorni(f"⚠ {vyroba_stav.JMENA[prog]} přestal hlásit – {d.get('nazev', '')}",
                                 f"Už {int(ticho // 60)} min žádná zpráva – asi spadl nebo se zavřelo okno. Poslední: {d.get('faze') or ''}", 5, ["warning"])


def _statistiky():
    try:
        import vyroba_statistiky  # noqa: E402
        vyroba_statistiky.spocitej()
    except Exception as e:  # noqa: BLE001
        print("statistiky:", e)


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
    stat_kontrola = 0.0
    render, pady_kontrola = Render(), 0.0
    snimek_kontrola = time.time() - SNIMEK_S + 30  # první snímek 30 s po startu
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
            # jméno projektu z titulku okna (žádný dotaz do Resolve); na obrazovce projektů se čas nepočítá
            r_bezi, r_bezi_s, r_projekt, r_pm, v_resolve = resolve_stav()
            nazev = r_projekt or (None if r_pm or not r_bezi else posledni["projekt"])
            aktivni = bool(v_resolve and r_projekt and necinnost_s() < NECINNOST_S)
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
                                          "resolve": r_bezi, "dnes": data.get(den, {}),
                                          "projekt_celkem": round(celkem, 1)})
                zapsano, zapsany_projekt = now, posledni["projekt"]
            # 📊 grafy ve Výrobě: statistiky se dřív přepočítaly jen na konci AUTO EDITU / DABINGU → když se jen
            # stříhalo v Resolve, grafy stály celé dny. Teď je přepočítá i sledování (po startu a každých 10 min).
            if now - stat_kontrola >= STAT_S:
                stat_kontrola = now
                threading.Thread(target=_statistiky, daemon=True).start()
            render.kontrola(r_bezi)  # (jen podle souboru v S:\EXPORT – do Resolve se nic neposílá)
            if getattr(render, "snimek", False) or now - snimek_kontrola >= SNIMEK_S:
                render.snimek, snimek_kontrola = False, now
                threading.Thread(target=posledni_snimek, daemon=True).start()
            if now - pady_kontrola >= 60:
                pady_kontrola = now
                hlidej_pady()
        except Exception as e:  # noqa: BLE001
            print("chyba:", e)
        time.sleep(KROK_S)


if __name__ == "__main__":
    zastav() if "--stop" in sys.argv else main()
