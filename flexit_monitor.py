#!/usr/bin/env python3
"""Revisa las ofertas públicas de Flexit y avisa por WhatsApp (CallMeBot) cuando aparecen nuevas."""

import json
import os
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

OFFERS_URL = "https://www.flexit.cl/api/offers"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.env"
SEEN_FILE = BASE_DIR / "seen_offers.json"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_config():
    config = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip().strip('"').strip("'")
    config.update({k: v for k, v in os.environ.items() if k.startswith("FLEXIT_")})
    return config


def normalize(text):
    """Minúsculas y sin tildes ni guiones, para comparar 'Bío-Bío' con 'biobio'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(c for c in text.lower() if c.isalnum())


def fetch_offers():
    req = urllib.request.Request(OFFERS_URL, headers={"User-Agent": "Mozilla/5.0 (flexit-monitor personal)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def offer_key(offer):
    fields = ("storeName", "jobName", "startDate", "endDate", "startTime", "endTime", "money")
    return "|".join(str(offer.get(f, "")) for f in fields)


def matches_filters(offer, config):
    jobs = [normalize(j) for j in config.get("FLEXIT_CARGOS", "").split(",") if j.strip()]
    stores = [normalize(s) for s in config.get("FLEXIT_TIENDAS", "").split(",") if s.strip()]
    min_pay = int(config.get("FLEXIT_PAGO_MINIMO", "0") or 0)
    if jobs and not any(j in normalize(offer.get("jobName", "")) for j in jobs):
        return False
    if stores and not any(s in normalize(offer.get("storeName", "")) for s in stores):
        return False
    return int(offer.get("money") or 0) >= min_pay


def format_date(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m")


def format_offer(offer):
    start, end = format_date(offer["startDate"]), format_date(offer["endDate"])
    dates = start if start == end else f"{start} al {end}"
    pay = f"${int(offer['money']):,}".replace(",", ".")
    return (
        f"*{offer['jobName']}*\n"
        f"{offer['storeName']}\n"
        f"{dates}, {offer['startTime'][:5]}-{offer['endTime'][:5]}\n"
        f"Pago: {pay}"
    )


def send_whatsapp(text, config):
    params = urllib.parse.urlencode({
        "phone": config["FLEXIT_TELEFONO"].replace(" ", ""),
        "text": text,
        "apikey": config["FLEXIT_APIKEY"],
    })
    with urllib.request.urlopen(f"{CALLMEBOT_URL}?{params}", timeout=30) as resp:
        body = resp.read().decode("utf-8", "ignore")
    if resp.status != 200 or "error" in body.lower():
        raise RuntimeError(f"CallMeBot respondió: {body[:300]}")


def main():
    config = load_config()
    if not config.get("FLEXIT_TELEFONO") or not config.get("FLEXIT_APIKEY"):
        log("Falta FLEXIT_TELEFONO o FLEXIT_APIKEY en config.env")
        return 1

    if "--test" in sys.argv:
        send_whatsapp("✅ Prueba del monitor de Flexit: los mensajes funcionan.", config)
        log("Mensaje de prueba enviado")
        return 0

    region = normalize(config.get("FLEXIT_REGION", "biobio"))
    data = fetch_offers()
    offers = [
        offer
        for state in data
        if region in normalize(state.get("stateName", ""))
        for offer in state.get("offers", [])
    ]
    if not offers:
        log(f"No hay ofertas para la región '{config.get('FLEXIT_REGION')}'")

    first_run = not SEEN_FILE.exists()
    seen = json.loads(SEEN_FILE.read_text()) if not first_run else {}
    today = datetime.now().strftime("%Y-%m-%d")
    new = []
    for offer in offers:
        key = offer_key(offer)
        if key not in seen:
            seen[key] = today
            if matches_filters(offer, config):
                new.append(offer)

    if new:
        header = "🟢 Monitor Flexit activado. Ofertas actuales:" if first_run else "🆕 Nueva oferta en Flexit:" if len(new) == 1 else f"🆕 {len(new)} nuevas ofertas en Flexit:"
        text = header + "\n\n" + "\n\n".join(format_offer(o) for o in new) + "\n\nPostula en la app de Flexit."
        send_whatsapp(text, config)
        log(f"Enviadas {len(new)} ofertas por WhatsApp")
    else:
        log("Sin ofertas nuevas")

    # Solo se guarda después de enviar, así si WhatsApp falla se reintenta en la próxima revisión.
    # Se borran las ofertas vistas hace más de 60 días para que el archivo no crezca sin fin.
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    seen = {k: d for k, d in seen.items() if d >= cutoff}
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"Error: {exc}")
        sys.exit(1)
