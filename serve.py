#!/usr/bin/env python3
"""
serve.py — Serveur local Izly avec auto-refresh du QR code.

Endpoints :
  GET  /          → PWA affichant le QR en temps réel (à ajouter à l'écran d'accueil iPhone)
  GET  /qr        → Image PNG du QR courant (cache-busted)
  GET  /api/status → JSON { validityDate, secondsLeft, isExpired }
  POST /api/refresh → Génère un nouveau QR et retourne { ok, validityDate, secondsLeft }
  GET  /pass      → Télécharge le .pkpass courant (si présent)

Usage :
    python serve.py --user taha.aziz5111@gmail.com --password 191004
    python serve.py --user ... --password ... --port 8080

Sur l'iPhone (même WiFi) :
  Safari → http://<IP>:8080  puis "Ajouter à l'écran d'accueil"

iOS Shortcut (1 tap = QR frais) :
  Raccourcis → Nouveau → "Obtenir le contenu de l'URL"
    URL : http://<IP>:8080/api/refresh   Méthode : POST
  → "Afficher le résultat" ou utiliser l'image renvoyée par /qr
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Désactive le buffering stdout pour les logs cloud (Railway, Render…)
sys.stdout.reconfigure(line_buffering=True)

# ── État global partagé (protégé par un lock) ────────────────────────────────
_lock         = threading.Lock()
_qr_bytes: bytes | None     = None
_validity_str: str | None   = None
_last_refresh: float        = 0.0   # timestamp unix

_QR_LIFETIME_SEC = 9 * 60   # on suppose ~10 min, on rafraîchit à 9 min


def _get_izly_session(username: str, password: str):
    """Retourne une session requests authentifiée."""
    sys.path.insert(0, str(Path(__file__).parent))
    from login_test import login
    return login(username, password)


def _do_refresh(session) -> tuple[bytes, str | None]:
    """Appelle l'API Izly et retourne (png_bytes, validity_str)."""
    from fetch_qr import fetch_qr_data_from_session, extract_image
    import tempfile, os

    data = fetch_qr_data_from_session(session)

    # extract_image écrit dans un fichier — on utilise un tmp
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    validity = extract_image(data, tmp)
    img = Path(tmp).read_bytes()
    os.unlink(tmp)
    return img, validity


def _seconds_left(validity_str: str | None) -> float | None:
    """Secondes restantes avant expiration du QR courant."""
    if not validity_str:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            exp = datetime.strptime(validity_str, fmt)
            return (exp - datetime.now()).total_seconds()
        except ValueError:
            continue
    return None


# ── Auto-refresh en arrière-plan ──────────────────────────────────────────────

def _background_refresher(username: str, password: str, margin: int):
    """Thread qui renouvelle le QR avant expiration."""
    global _qr_bytes, _validity_str, _last_refresh

    print("  Connexion à Izly…")
    session = _get_izly_session(username, password)
    print("  Connexion OK.")

    while True:
        try:
            img, validity = _do_refresh(session)
            with _lock:
                _qr_bytes     = img
                _validity_str = validity
                _last_refresh = time.time()
            left = _seconds_left(validity)
            ts   = datetime.now().strftime("%H:%M:%S")
            if left:
                print(f"  [{ts}] QR renouvelé — valide jusqu'à {validity or '?'} ({left:.0f}s restantes)")
            else:
                print(f"  [{ts}] QR renouvelé — valide jusqu'à {validity or '?'}")
            sleep = max(30, (left - margin)) if left else (9 * 60 - margin)
        except Exception as exc:
            print(f"  [WARN] Erreur refresh : {exc}")
            # Reconnexion automatique si session expirée
            if "SessionExpired" in str(exc) or "401" in str(exc) or "403" in str(exc):
                print("  [INFO] Session expirée — reconnexion dans 5s…")
                time.sleep(5)
                try:
                    session = _get_izly_session(username, password)
                    print("  [INFO] Reconnexion OK.")
                except Exception as e2:
                    print(f"  [ERR] Reconnexion impossible : {e2} — retry dans 30s")
                    time.sleep(30)
            else:
                print("  [INFO] Retry dans 30s")
                sleep = 30
            continue

        time.sleep(sleep)


# ── HTML de la PWA ────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Izly Pay">
<title>Izly Pay</title>
<style>
  *    { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, sans-serif; background: #005dab;
         min-height: 100dvh; display: flex; flex-direction: column;
         align-items: center; justify-content: center; padding: 2rem 1rem;
         padding-top: calc(2rem + env(safe-area-inset-top)); }
  h1   { color: #fff; font-size: 1.6rem; margin-bottom: .3rem; }
  #subtitle { color: rgba(255,255,255,.7); font-size: .9rem; margin-bottom: 1.5rem; }
  #card { background: #fff; border-radius: 20px;
          box-shadow: 0 8px 32px rgba(0,0,0,.25);
          padding: 1.5rem; width: 100%; max-width: 320px; text-align: center; }
  #qr-img { width: 100%; border-radius: 10px; }
  #qr-placeholder { width: 100%; aspect-ratio: 1; border-radius: 10px;
                    background: #f2f2f7; display: flex; align-items: center;
                    justify-content: center; font-size: 2rem; }
  #validity { margin-top: .8rem; font-size: .85rem; color: #555; }
  #countdown { font-weight: 700; color: #005dab; }
  #countdown.warn  { color: #ff9500; }
  #countdown.urgent{ color: #ff3b30; }
  #btn-refresh { margin-top: 1rem; width: 100%; padding: .9rem;
                 background: #005dab; color: #fff; border: none;
                 border-radius: 12px; font-size: 1rem; font-weight: 600;
                 cursor: pointer; transition: opacity .15s; }
  #btn-refresh:active { opacity: .7; }
  #btn-refresh:disabled { opacity: .4; cursor: default; }
  #status { margin-top: .6rem; font-size: .78rem; color: #888; min-height: 1em; }
</style>
</head>
<body>
  <h1>Izly Pay</h1>
  <p id="subtitle">QR code de paiement</p>

  <div id="card">
    <div id="qr-placeholder">⏳</div>
    <img id="qr-img" alt="QR Code Izly" style="display:none">
    <p id="validity">Chargement…</p>
    <p id="countdown"></p>
    <button id="btn-refresh" onclick="refresh()">🔄 Nouveau QR</button>
    <p id="status"></p>
  </div>

<script>
let countdownTimer = null;
let expiresAt = null;

async function refresh() {
  const btn = document.getElementById('btn-refresh');
  btn.disabled = true;
  btn.textContent = '⏳ Génération…';
  document.getElementById('status').textContent = '';
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      loadQr();
      startCountdown(d.secondsLeft);
      document.getElementById('validity').textContent =
        'Valide jusqu\\'à ' + (d.validityDate || '~10 min');
      document.getElementById('status').textContent = '✓ QR renouvelé';
    } else {
      document.getElementById('status').textContent = '⚠ Erreur : ' + (d.error || 'inconnue');
    }
  } catch(e) {
    document.getElementById('status').textContent = '⚠ Serveur inaccessible';
  }
  btn.disabled = false;
  btn.textContent = '🔄 Nouveau QR';
}

function loadQr() {
  const img = document.getElementById('qr-img');
  const ph  = document.getElementById('qr-placeholder');
  img.src = '/qr?t=' + Date.now();
  img.onload = () => { img.style.display = 'block'; ph.style.display = 'none'; };
}

function startCountdown(seconds) {
  if (countdownTimer) clearInterval(countdownTimer);
  if (!seconds || seconds <= 0) return;
  expiresAt = Date.now() + seconds * 1000;
  countdownTimer = setInterval(() => {
    const left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
    const el   = document.getElementById('countdown');
    const m    = Math.floor(left / 60);
    const s    = left % 60;
    el.textContent = `⏱ ${m}:${String(s).padStart(2,'0')} restantes`;
    el.className = left < 60 ? 'urgent' : left < 120 ? 'warn' : '';
    if (left === 0) { clearInterval(countdownTimer); refresh(); }
  }, 1000);
}

async function init() {
  const r = await fetch('/api/status').catch(() => null);
  if (r && r.ok) {
    const d = await r.json();
    if (!d.isExpired && d.secondsLeft > 0) {
      loadQr();
      startCountdown(d.secondsLeft);
      document.getElementById('validity').textContent =
        'Valide jusqu\\'à ' + (d.validityDate || '?');
      document.getElementById('status').textContent = '✓ QR actif';
      return;
    }
  }
  refresh();
}

init();
</script>
</body>
</html>"""

_HTML_BYTES = _HTML.encode("utf-8")


# ── Gestionnaire HTTP ─────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Silencieux sauf erreurs
        if int(args[1]) >= 400:
            print(f"  [{self.address_string()}] {fmt % args}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", _HTML_BYTES)

        elif path == "/health":
            self._send(200, "application/json", json.dumps({"ok": True}).encode())

        elif path == "/qr":
            with _lock:
                data = _qr_bytes
            if data:
                self._send(200, "image/png", data,
                           extra={"Cache-Control": "no-store"})
            else:
                self._send(503, "text/plain", b"QR not ready yet")

        elif path == "/api/status":
            with _lock:
                v = _validity_str
                q = _qr_bytes
            left = _seconds_left(v)
            payload = {
                "validityDate": v,
                "secondsLeft":  round(left) if left is not None else None,
                "isExpired":    (left is not None and left <= 0) or q is None,
            }
            self._send(200, "application/json",
                       json.dumps(payload).encode())

        elif path == "/pass":
            p = Path("izly.pkpass")
            if p.exists():
                self._send(200, "application/vnd.apple.pkpass", p.read_bytes(),
                           extra={"Content-Disposition":
                                  'attachment; filename="izly.pkpass"'})
            else:
                self._send(404, "text/plain", b"No pkpass available")

        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/refresh":
            # Le refresh réel se fait dans le thread background.
            # Ici on attend juste que le QR soit dispo (max 15 s).
            deadline = time.time() + 15
            while time.time() < deadline:
                with _lock:
                    q = _qr_bytes
                    v = _validity_str
                if q:
                    left = _seconds_left(v)
                    payload = {
                        "ok":           True,
                        "validityDate": v,
                        "secondsLeft":  round(left) if left else None,
                    }
                    self._send(200, "application/json",
                               json.dumps(payload).encode())
                    return
                time.sleep(0.5)
            self._send(503, "application/json",
                       json.dumps({"ok": False,
                                   "error": "timeout"}).encode())
        else:
            self._send(404, "text/plain", b"Not found")

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(
        description="Serveur local Izly — PWA + API pour iPhone"
    )
    parser.add_argument("--user",     required=True, metavar="LOGIN",
                        help="Identifiant Izly (e-mail ou tél.)")
    parser.add_argument("--password", default=None,  metavar="PWD",
                        help="Code secret Izly (omis = saisie masquée)")
    parser.add_argument("--port", default=None, type=int,
                        help="Port HTTP (défaut : $PORT ou 8080)")
    parser.add_argument("--margin",   default=90, type=int,
                        help="Secondes avant expiration pour pré-renouveler (défaut : 90)")
    args = parser.parse_args()

    # Port : argument CLI > variable d'environnement $PORT > 8080
    port = args.port or int(os.environ.get("PORT", 8080))

    if not args.password:
        import getpass
        args.password = getpass.getpass("Code secret Izly : ")

    ip  = get_local_ip()
    url = f"http://{ip}:{port}"

    print(f"\nIzly QR Server")
    print(f"  Compte  : {args.user}")
    print(f"  Port    : {port}")
    print(f"  Marge   : {args.margin}s avant expiration\n")

    # Lance le thread de refresh en arrière-plan
    t = threading.Thread(
        target=_background_refresher,
        args=(args.user, args.password, args.margin),
        daemon=True,
    )
    t.start()

    print(f"\nServeur démarré → {url}")
    print(f"Sur l'iPhone (même WiFi) : Safari → {url}")
    print(f"Endpoints :")
    print(f"  GET  {url}/        → PWA")
    print(f"  GET  {url}/qr      → PNG")
    print(f"  POST {url}/api/refresh")
    print(f"  GET  {url}/health  → healthcheck")
    print("Ctrl+C pour arrêter.\n")

    server = HTTPServer(("0.0.0.0", port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
        sys.exit(0)


if __name__ == "__main__":
    main()

