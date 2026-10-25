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
