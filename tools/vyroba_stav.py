"""🎬 Výroba – průběh DABINGU a AUTO EDITU pro web Brawl Trendy (záložka 🎬 Výroba, i z mobilu).

Zapisuje malý JSON (vyroba_dabing.json / vyroba_edit.json) do složky Google Disku „Brawl Trendy – data“
na disku G: – Google Drive pro počítač ho sám nahraje a web si ho přečte. Nic se neposílá nikam jinam.
Když G: / složka není, modul tiše nic nedělá. Žádná chyba odsud nesmí shodit dabing ani edit.

Použití:
    import vyroba_stav
    WEB = vyroba_stav.Stav("edit", "Název projektu")
    WEB.radek("▶ Krok 3/22: přepis řeči")      # řádek výpisu (posledních 40 se ukazuje na webu)
    WEB.nastav(procenta=12.5, zbyva_s=840)     # cokoli dalšího, co má web ukázat
    WEB.konec(True, "✔ HOTOVO za 14 min")
"""
import json
import os
import threading
import time

SLOZKA = r"G:\Můj disk\Brawl Trendy – data"
# Google Drive pro počítač nahrávání PŘERUŠÍ, když se do souboru během nahrávání zapíše („Stream was written to“)
# → při zápisu co 5 s se nenahrálo nikdy nic. Proto nejvýš co 15 s a vždy celý nový soubor najednou (os.replace).
KAZDYCH_S = 15          # nejvýš tak často se soubor přepíše (Drive ho pak nahraje)
ZIVOT_S = 45            # i beze změny se tak často zapíše „žiju“ – web pozná, že program nespadl
HISTORIE_KAZDYCH_S = 20  # bod do grafu průběhu (čas, procenta)
RADKU = 40


WEB_URL = "https://samuel440.github.io/brawl-trendy/#vyr"
NTFY_SOUBOR = r"S:\EDIT\_cache\ntfy_majitel.txt"


def ntfy_kanal() -> str:
    """Tajný kanál ntfy pro Samkův mobil (vymyslí se jednou, uloží na PC a web ho ukáže ve Výrobě k přihlášení)."""
    try:
        k = open(NTFY_SOUBOR, encoding="utf-8").read().strip()
        if k:
            return k
    except Exception:  # noqa: BLE001
        pass
    import secrets
    k = "ultrasami-vyroba-" + secrets.token_hex(8)
    try:
        os.makedirs(os.path.dirname(NTFY_SOUBOR), exist_ok=True)
        open(NTFY_SOUBOR, "w", encoding="utf-8").write(k)
    except Exception:  # noqa: BLE001
        pass
    return k


def _ssl_kontexty():
    import ssl
    try:
        import certifi
        yield ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    try:
        import truststore
        yield truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        pass
    yield ssl.create_default_context()


def upozorni(titulek: str, text: str = "", priorita: int = 4, tagy=None):
    """📲 Upozornění na mobil přes ntfy.sh (appka ntfy, zdarma) – ve vlákně, nikdy nic neshodí a nezdrží."""
    def _posli():
        try:
            import urllib.request
            kanal = ntfy_kanal()
            uloz("upozorneni", {"t": int(time.time() * 1000), "kanal": kanal})  # web ukáže, kam se přihlásit
            data = json.dumps({"topic": kanal, "title": titulek[:200], "message": (text or titulek)[:1000],
                               "priority": priorita, "tags": tagy or [], "click": WEB_URL}).encode("utf-8")
            req = urllib.request.Request("https://ntfy.sh/", data=data, method="POST", headers={"Content-Type": "application/json"})
            # Python na PC má zastaralé kořenové certifikáty („certificate has expired“) → certifi / truststore, co je po ruce
            for ctx in _ssl_kontexty():
                try:
                    urllib.request.urlopen(req, timeout=15, context=ctx).read()
                    return
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_posli, daemon=True).start()


JMENA = {"edit": "✂️ AUTO EDIT", "dabing": "🎙️ DABING", "render": "🎬 RENDER"}


def uloz(nazev: str, data) -> bool:
    """Jednorázově uloží libovolná data jako vyroba_<nazev>.json (např. snímky timeliny z Resolve)."""
    try:
        if not os.path.isdir(SLOZKA):
            return False
        tmp = os.path.join(SLOZKA, f"~vyr_{nazev}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        os.replace(tmp, os.path.join(SLOZKA, f"vyroba_{nazev}.json"))
        return True
    except Exception:  # noqa: BLE001
        return False


class Stav:
    def __init__(self, program: str, nazev: str):
        self.soubor = os.path.join(SLOZKA, f"vyroba_{program}.json")
        now = time.time()
        self.d = {"program": program, "nazev": nazev, "start": int(now * 1000), "t": 0, "stav": "bezi",
                  "procenta": 0.0, "zbyva_s": None, "faze": "", "radky": [], "historie": [[int(now * 1000), 0.0]]}
        self.lock = threading.RLock()
        self.zapsano = 0.0
        self.zmena = True
        self.bezi = True
        self._vlakno = threading.Thread(target=self._smycka, daemon=True)
        self._vlakno.start()
        self._zapis(True)

    # -- změny
    def nastav(self, **kw):
        try:
            with self.lock:
                self.d.update(kw)
                p = self.d.get("procenta") or 0
                h = self.d["historie"]
                now = int(time.time() * 1000)
                if now - h[-1][0] >= HISTORIE_KAZDYCH_S * 1000 and abs(p - h[-1][1]) >= 0.1:
                    h.append([now, round(p, 1)])
                    if len(h) > 600:
                        del h[1:len(h) - 599]
                self.zmena = True
            self._zapis()
        except Exception:  # noqa: BLE001
            pass

    def radek(self, text: str):
        try:
            text = str(text).strip("\r\n")
            if not text.strip():
                return
            with self.lock:
                r = self.d["radky"]
                for ln in text.splitlines():
                    if ln.strip():
                        r.append([int(time.time() * 1000), ln[:300]])
                del r[:-RADKU]
                self.zmena = True
            self._zapis()
        except Exception:  # noqa: BLE001
            pass

    def konec(self, ok: bool, zprava: str = ""):
        try:
            with self.lock:
                self.d.update(stav="hotovo" if ok else "chyba", konec=int(time.time() * 1000), zprava=zprava)
                if ok:
                    self.d["procenta"] = 100.0
                    self.d["zbyva_s"] = 0
                    self.d["historie"].append([int(time.time() * 1000), 100.0])
                self.bezi = False
            self._zapis(True)
            # 📲 konec běhu na mobil: chyba hlasitě, hotovo normálně
            jm = JMENA.get(self.d["program"], self.d["program"])
            if ok:
                upozorni(f"✔ {jm} hotovo – {self.d.get('nazev', '')}", zprava or "Hotovo.", 3, ["white_check_mark"])
            else:
                upozorni(f"✖ {jm} – CHYBA – {self.d.get('nazev', '')}", zprava or "Program skončil chybou.", 5, ["rotating_light"])
        except Exception:  # noqa: BLE001
            pass

    # -- zápis na G:
    def _smycka(self):
        while self.bezi:
            time.sleep(KAZDYCH_S + 1)
            if self.bezi:
                self._zapis(time.time() - self.zapsano >= ZIVOT_S)

    def _zapis(self, vynutit=False):
        try:
            if not os.path.isdir(SLOZKA):
                return
            with self.lock:
                if not vynutit and (not self.zmena or time.time() - self.zapsano < KAZDYCH_S):
                    return
                self.d["t"] = int(time.time() * 1000)
                data = json.dumps(self.d, ensure_ascii=False)
                self.zmena = False
                self.zapsano = time.time()
            # celý soubor bokem a pak jedním krokem na místo – Drive vidí jen hotové verze
            tmp = os.path.join(SLOZKA, f"~vyr_{self.d['program']}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.soubor)
        except Exception:  # noqa: BLE001
            pass
