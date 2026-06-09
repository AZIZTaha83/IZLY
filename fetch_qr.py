#!/usr/bin/env python3
"""
fetch_qr.py — Récupère le QR code de paiement Izly depuis une session navigateur.

Le script reproduit exactement l'appel AJAX que la page Izly effectue :
  POST /Home/CreateQrCodeImg  →  { images: ["<base64_png>", ...], validityDate: "..." }

Usage :
    python fetch_qr.py
    python fetch_qr.py --cookies cookies.txt --output izly_qr.png
    python fetch_qr.py --json-dump          # affiche la réponse brute de l'API

Prérequis :
    pip install requests
    Un fichier cookies.txt au format Netscape (export via l'extension "Get cookies.txt LOCALLY"
    sur https://mon-espace.izly.fr).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import http.cookiejar
import json
import sys
from pathlib import Path

import requests

IZLY_BASE = "https://mon-espace.izly.fr"
QR_ENDPOINT = f"{IZLY_BASE}/Home/CreateQrCodeImg"

_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": IZLY_BASE,
    "Referer": f"{IZLY_BASE}/Home/Accueil",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


# ── Chargement des cookies ─────────────────────────────────────────────────────

def load_cookies(cookie_file: str) -> requests.cookies.RequestsCookieJar:
    """Charge un fichier cookies Netscape dans un RequestsCookieJar."""
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        sys.exit(
            f"Fichier introuvable : {cookie_file!r}\n"
            "  1. Connecte-toi à https://mon-espace.izly.fr\n"
            "  2. Installe l'extension 'Get cookies.txt LOCALLY' (Chrome/Edge)\n"
            "  3. Exporte les cookies du domaine dans cookies.txt"
        )
    except http.cookiejar.LoadError as exc:
        sys.exit(f"Impossible de lire {cookie_file!r} : {exc}")

    req_jar = requests.cookies.RequestsCookieJar()
    for c in jar:
        req_jar.set(c.name, c.value, domain=c.domain, path=c.path)
    return req_jar


# ── Appel API ──────────────────────────────────────────────────────────────────

def fetch_qr_data(cookie_file: str) -> dict:
    """
    Appelle POST /Home/CreateQrCodeImg et retourne le JSON de réponse.
    Lève SystemExit si la session est invalide ou expirée.
    """
    session = requests.Session()
    session.cookies = load_cookies(cookie_file)
    return _call_qr_endpoint(session)


def fetch_qr_data_from_session(session: requests.Session) -> dict:
    """Même appel mais depuis une session déjà authentifiée (login direct)."""
    return _call_qr_endpoint(session)


def _call_qr_endpoint(session: requests.Session) -> dict:
    try:
        resp = session.post(
            QR_ENDPOINT,
            headers=_HEADERS,
            data={"numberOfQrCodes": "1"},
            timeout=15,
            allow_redirects=False,
        )
    except requests.ConnectionError as exc:
        sys.exit(f"Impossible de joindre Izly : {exc}")
    except requests.Timeout:
        sys.exit("Timeout — le serveur Izly n'a pas répondu dans les 15 secondes.")

    if resp.status_code in (301, 302):
        sys.exit(
            "Redirection détectée : session expirée ou non connectée.\n"
            "Reconnecte-toi et exporte à nouveau les cookies."
        )
    if resp.status_code in (401, 403):
        sys.exit(f"Accès refusé (HTTP {resp.status_code}). Session invalide.")

    if resp.status_code == 500:
        sys.exit(
            f"Erreur 500 du serveur Izly.\n"
            f"Cookies envoyés : {[c.name for c in session.cookies]}\n"
            f"Réponse (500 chars) : {resp.text[:500]}"
        )

    resp.raise_for_status()

    try:
        return resp.json()
    except ValueError:
        sys.exit(f"Réponse non-JSON reçue :\n{resp.text[:500]}")


# ── Extraction de l'image ──────────────────────────────────────────────────────

def extract_image(data: dict, output: str) -> str | None:
    """
    Extrait la première image Base64 du JSON et la sauvegarde en PNG.
    Retourne la validityDate, ou None si absente.

    L'API Izly peut retourner :
      { "images": ["<b64>", ...], "validityDate": "..." }
    ou
      { "data": { "images": [...], "validityDate": "..." } }
    """
    payload: dict = data.get("data", data)
    images: list[str] = payload.get("images", [])

    if not images:
        sys.exit(
            "Aucune image dans la réponse de l'API.\n"
            "Réponse brute :\n" + json.dumps(data, indent=2, ensure_ascii=False)
        )

    img_b64: str = images[0]
    # Supprime le préfixe data URI si présent : "data:image/png;base64,..."
    if "," in img_b64:
        img_b64 = img_b64.split(",", 1)[1]

    img_bytes = base64.b64decode(img_b64)
    Path(output).write_bytes(img_bytes)

    return payload.get("validityDate")


# ── Point d'entrée ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Récupère le QR code de paiement Izly et le sauvegarde en PNG."
    )
    parser.add_argument(
        "--cookies", default="cookies.txt", metavar="FILE",
        help="Fichier cookies Netscape (défaut : cookies.txt)"
    )
    parser.add_argument(
        "--output", default="izly_qr.png", metavar="FILE",
        help="Image PNG de sortie (défaut : izly_qr.png)"
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Se connecte avec identifiant + mot de passe au lieu d'utiliser cookies.txt"
    )
    parser.add_argument(
        "--user", default=None, metavar="LOGIN",
        help="Identifiant Izly (avec --login)"
    )
    parser.add_argument(
        "--password", default=None, metavar="PWD",
        help="Mot de passe Izly (avec --login ; omis = saisie masquée)"
    )
    parser.add_argument(
        "--json-dump", action="store_true",
        help="Affiche la réponse JSON brute de l'API Izly"
    )
    args = parser.parse_args()

    print("Récupération du QR code Izly…")

    if args.login:
        # Connexion directe — pas de fichier cookies
        from login_test import login as _login
        username = args.user or input("Identifiant Izly : ").strip()
        password = args.password or getpass.getpass("Mot de passe : ")
        session = _login(username, password)
        data = fetch_qr_data_from_session(session)
    else:
        data = fetch_qr_data(args.cookies)

    if args.json_dump:
        print(json.dumps(data, indent=2, ensure_ascii=False))

    validity = extract_image(data, args.output)

    print(f"[OK] QR code enregistré : {args.output}")
    if validity:
        print(f"[OK] Valide jusqu'à     : {validity}")
    else:
        print("[!]  Durée de validité  : ~10 minutes (non fournie par l'API)")

    print()
    print("Etape suivante :")
    print("  python create_pass.py --qr izly_qr.png")


if __name__ == "__main__":
    main()
