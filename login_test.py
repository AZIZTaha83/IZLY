#!/usr/bin/env python3
"""
login_test.py — Vérifie la connexion à Izly via identifiant + mot de passe.

Usage :
    python login_test.py
    python login_test.py --user 0612345678 --password MonMotDePasse
    python login_test.py --save-cookies cookies.txt   # exporte la session pour les autres scripts

Prérequis :
    pip install requests
"""

from __future__ import annotations

import argparse
import getpass
import sys

import requests

IZLY_BASE   = "https://mon-espace.izly.fr"
LOGIN_PAGE  = f"{IZLY_BASE}/Account/Login"   # GET — page avec le formulaire + CSRF
LOGIN_URL   = f"{IZLY_BASE}/Home/Logon"      # POST — action réelle du formulaire
ACCOUNT_URL = f"{IZLY_BASE}/Home/Accueil"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _get_antiforgery_token(session: requests.Session) -> str | None:
    """
    Charge la page de login et extrait le token anti-CSRF
    (champ caché __RequestVerificationToken).
    """
    resp = session.get(LOGIN_URL, headers=_HEADERS, timeout=15)
    resp.raise_for_status()

    # Recherche basique du token dans le HTML — pas besoin de BeautifulSoup
    marker = 'name="__RequestVerificationToken"'
    idx = resp.text.find(marker)
    if idx == -1:
        return None

    # …value="<token>"…
    value_start = resp.text.find('value="', idx) + len('value="')
    value_end   = resp.text.find('"', value_start)
    return resp.text[value_start:value_end]


def login(username: str, password: str, debug: bool = False) -> requests.Session:
    """
    Ouvre une session authentifiée sur mon-espace.izly.fr.
    Lève SystemExit si la connexion échoue.
    """
    session = requests.Session()

    print("  1/3 Chargement de la page de connexion…")
    try:
        resp_get = session.get(LOGIN_PAGE, headers=_HEADERS, timeout=15)
        resp_get.raise_for_status()
    except requests.ConnectionError as exc:
        sys.exit(f"Impossible de joindre Izly : {exc}")
    except requests.Timeout:
        sys.exit("Timeout — le serveur Izly n'a pas répondu.")

    if debug:
        print(f"  [debug] GET {LOGIN_PAGE} → HTTP {resp_get.status_code}")
        print(f"  [debug] Cookies après GET : {[c.name for c in session.cookies]}")

    token = None
    marker = 'name="__RequestVerificationToken"'
    idx = resp_get.text.find(marker)
    if idx != -1:
        value_start = resp_get.text.find('value="', idx) + len('value="')
        value_end   = resp_get.text.find('"', value_start)
        token = resp_get.text[value_start:value_end]

    if debug:
        print(f"  [debug] Token CSRF trouvé : {'oui' if token else 'NON — champ absent'}")

    print("  2/3 Envoi des identifiants…")
    payload: dict = {
        "Username":   username,
        "Password":   password,
        "RememberMe": "false",
    }
    if token:
        payload["__RequestVerificationToken"] = token

    resp = session.post(
        LOGIN_URL,
        data=payload,
        headers={
            **_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LOGIN_PAGE,
        },
        timeout=15,
        allow_redirects=True,
    )

    if debug:
        print(f"  [debug] POST → HTTP {resp.status_code}")
        print(f"  [debug] URL finale : {resp.url}")
        print(f"  [debug] Cookies après POST : {[c.name for c in session.cookies]}")
        print(f"  [debug] Extrait de la réponse (500 premiers chars) :")
        print("  " + resp.text[:500].replace("\n", "\n  "))

    resp.raise_for_status()

    print("  3/3 Vérification de la session…")

    from urllib.parse import urlparse
    final_path = urlparse(resp.url).path
    still_on_login = final_path.rstrip("/") in ("/Account/Login", "/Home/Logon") and (
        'name="Username"' in resp.text and 'name="Password"' in resp.text
    )
    has_login_form = 'name="Username"' in resp.text and 'name="Password"' in resp.text

    if debug:
        print(f"  [debug] Toujours sur /Login : {still_on_login}")
        print(f"  [debug] Formulaire de login présent : {has_login_form}")

    if still_on_login or has_login_form:
        # Essaie d'extraire le message d'erreur affiché par Izly
        err_msg = ""
        for marker_err in ('validation-summary-errors', 'field-validation-error', 'text-danger'):
            mi = resp.text.find(marker_err)
            if mi != -1:
                snippet_start = resp.text.find('>', mi) + 1
                snippet_end   = resp.text.find('<', snippet_start)
                err_msg = resp.text[snippet_start:snippet_end].strip()
                if err_msg:
                    break

        msg = "\n[ECHEC] La connexion a échoué."
        if err_msg:
            msg += f"\n  Message Izly : {err_msg}"
        else:
            msg += "\n  Vérifie ton identifiant (tél. ou e-mail) et ton mot de passe."
        msg += "\n\n  Relance avec --debug pour voir la réponse complète du serveur."
        sys.exit(msg)

    return session


def save_cookies(session: requests.Session, path: str) -> None:
    """Sauvegarde les cookies de session au format Netscape (compatible cookies.txt)."""
    import http.cookiejar
    jar = http.cookiejar.MozillaCookieJar(path)
    for c in session.cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True, ignore_expires=True)
    print(f"  Cookies sauvegardés → {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Teste la connexion à Izly avec identifiant + mot de passe."
    )
    parser.add_argument("--user",     default=None, metavar="LOGIN",
                        help="Numéro de téléphone ou e-mail Izly")
    parser.add_argument("--password", default=None, metavar="PWD",
                        help="Mot de passe (omis = saisie masquée)")
    parser.add_argument("--save-cookies", default=None, metavar="FILE",
                        help="Exporte les cookies de session dans ce fichier "
                             "(utilisable avec fetch_qr.py)")
    parser.add_argument("--debug", action="store_true",
                        help="Affiche les détails HTTP de chaque échange")
    args = parser.parse_args()

    username = args.user or input("Identifiant Izly (tél. ou e-mail) : ").strip()
    password = args.password or getpass.getpass("Mot de passe : ")

    print("\nConnexion à Izly…")
    session = login(username, password, debug=args.debug)

    print("\n[OK] Connexion réussie !")
    print(f"  URL finale : {ACCOUNT_URL}")

    # Affiche les cookies obtenus (noms uniquement, pas les valeurs)
    cookie_names = [c.name for c in session.cookies]
    print(f"  Cookies    : {', '.join(cookie_names) if cookie_names else '(aucun)'}")

    if args.save_cookies:
        save_cookies(session, args.save_cookies)
        print()
        print("Etape suivante :")
        print(f"  python fetch_qr.py --cookies {args.save_cookies}")
    else:
        print()
        print("Conseil : relance avec --save-cookies cookies.txt pour exporter la session.")


if __name__ == "__main__":
    main()
