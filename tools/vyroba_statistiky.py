"""📊 Statistiky pro záložku 🛠️ Výroba: kolik času jsi strávil na kterém projektu (podle záloh DaVinci Resolve)
a kolik stály AI za AUTO EDIT a DABING – po dnech i celkem. Zapíše vyroba_statistiky.json na G: (web si ho přečte).

Čas: Resolve si během práce každých pár minut ukládá zálohu projektu (S:\\Resolve Project Backups\\<id projektu>\\
Project.db.RRRRMMDDHHMMSS). Mezery mezi zálohami do 25 min = pracoval jsi; delší mezera = pauza.
Peníze: AUTO EDIT zapisuje každý AI dotaz do S:\\EDIT\\_cache\\ai_costs.jsonl, DABING píše „Claude: $X“ do svého log.txt.
Spouští se samo na konci Auto Editu a dabingu a při běhu NÁHLED.bat (co 30 min). Nic neposílá jinam.
"""
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import sys
import time

ZALOHY = r"S:\Resolve Project Backups"
JMENA = r"S:\EDIT\_cache\projekty_jmena.json"   # id projektu -> jméno (čte se z Project.db jen jednou)
AI_LOG = r"S:\EDIT\_cache\ai_costs.jsonl"
DAB_LOG = r"S:\DABING\_pro_vyvojare\log.txt"
DNI = 120          # jak daleko do minulosti
MEZERA_MIN = 25    # delší pauza mezi zálohami = nepracoval
ZACATEK_MIN = 5    # první záloha sezení = aspoň 5 min práce před ní
RX_TS = re.compile(r"\.(\d{14})$")


def _den(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _jmeno(folder, cache):
    pid = os.path.basename(folder)
    if pid in cache:
        return cache[pid]
    dbs = sorted(glob.glob(os.path.join(folder, "Project.db.*")))
    name = None
    if dbs:
        try:
            con = sqlite3.connect("file:" + dbs[-1] + "?mode=ro", uri=True)
            row = con.execute('select ProjectName from "SM_Project" limit 1').fetchone()
            con.close()
            name = row[0] if row else None
        except Exception:  # noqa: BLE001
            name = None
    cache[pid] = name
    return name


def cas_prace():
    """{den: {projekt: minuty}} z časů záloh Resolve."""
    try:
        cache = json.load(open(JMENA, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cache = {}
    od = time.time() - DNI * 86400
    out = {}
    for e in os.scandir(ZALOHY) if os.path.isdir(ZALOHY) else []:
        if not e.is_dir() or e.stat().st_mtime < od:
            continue
        ts = []
        for root, _, files in os.walk(e.path):
            for f in files:
                m = RX_TS.search(f)
                if m:
                    try:
                        t = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S").timestamp()
                    except ValueError:
                        continue
                    if t >= od:
                        ts.append(t)
        if not ts:
            continue
        name = _jmeno(e.path, cache) or os.path.basename(e.path)[:8]
        ts = sorted(set(round(t / 30) * 30 for t in ts))  # (projekt + timeline záloha ve stejnou chvíli = jedna)
        prev = None
        for t in ts:
            gap = (t - prev) / 60 if prev else None
            minut = gap if gap is not None and gap <= MEZERA_MIN else ZACATEK_MIN
            d = out.setdefault(_den(t), {})
            d[name] = round(d.get(name, 0) + minut, 1)
            prev = t
    try:
        json.dump(cache, open(JMENA, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    # přesné minuty ze sledování (vyroba_sledovani.py) mají přednost před odhadem ze záloh – po dnech
    try:
        for den, d in json.load(open(r"S:\EDIT\_cache\cas_log.json", encoding="utf-8")).items():
            if d:
                out[den] = {k: float(v) for k, v in d.items()}
    except Exception:  # noqa: BLE001
        pass
    return out


def penize():
    """[(den, 'edit'|'dabing', projekt, usd)]"""
    rows = []
    try:
        for ln in open(AI_LOG, encoding="utf-8"):
            try:
                e = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if e.get("base") == "TEST":
                continue
            rows.append((_den(e["ts"]), "edit", os.path.basename(e.get("base") or "") or "Auto Edit", float(e.get("usd") or 0)))
    except Exception:  # noqa: BLE001
        pass
    try:
        video = "DABING"
        for ln in open(DAB_LOG, encoding="utf-8", errors="replace"):
            m = re.search(r"=== (.+?) ===", ln)
            if m:
                video = os.path.splitext(m.group(1))[0]
            m = re.match(r"(\d{4}-\d{2}-\d{2}) .*Celkový čas: .*Claude: \$([\d.]+)", ln)
            if m:
                rows.append((m.group(1), "dabing", video, float(m.group(2))))
    except Exception:  # noqa: BLE001
        pass
    return rows


def kurz():
    try:
        sys.path.insert(0, r"S:\EDIT\tools")
        import ai  # noqa: E402
        return round(ai.czk_rate(), 3)
    except Exception:  # noqa: BLE001
        return 23.0


def spocitej(zapsat=True):
    cas = cas_prace()
    rows = penize()
    den, proj, celkem, den_proj = {}, {}, {"edit": 0.0, "dabing": 0.0}, {}
    for d, druh, p, usd in rows:
        den.setdefault(d, {"edit": 0.0, "dabing": 0.0})[druh] += usd
        proj.setdefault(p, {"edit": 0.0, "dabing": 0.0})[druh] += usd
        den_proj.setdefault(d, {}).setdefault(p, {"edit": 0.0, "dabing": 0.0})[druh] += usd
        celkem[druh] += usd
    r3 = lambda x: {k: round(v, 3) for k, v in x.items()}  # noqa: E731
    data = {"t": int(time.time() * 1000), "kurz": kurz(), "cas": cas,
            "penize_den": {d: r3(v) for d, v in sorted(den.items())}, "penize_projekt": {p: r3(v) for p, v in proj.items()},
            "penize_den_projekt": {d: {p: r3(v) for p, v in ps.items()} for d, ps in den_proj.items()},
            "penize_celkem": r3(celkem)}
    if zapsat:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import vyroba_stav
            vyroba_stav.uloz("statistiky", data)
        except Exception:  # noqa: BLE001
            pass
    return data


if __name__ == "__main__":
    d = spocitej()
    dnes = dt.date.today().isoformat()
    print("dnes:", d["cas"].get(dnes), "| celkem $", d["penize_celkem"], "| kurz", d["kurz"])
