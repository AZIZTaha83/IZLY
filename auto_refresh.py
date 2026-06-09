#!/usr/bin/env python3
"""
auto_refresh.py — Renouvelle automatiquement le QR code Izly avant expiration
et reconstruit le .pkpass à la volée.

Le script :
  1. Appelle fetch_qr.fetch_qr_data() pour obtenir un QR frais.
  2. Sauvegarde l'image PNG.
  3. Appelle create_pass.build_pkpass() pour rebâtir le .pkpass.
  4. Dort jusqu'à (expiry - margin) secondes, puis recommence.

Usage :
    python auto_refresh.py
    python auto_refresh.py --cookies cookies.txt --margin 60 --output-dir ./passes

    # Avec signature Apple Wallet :
    python auto_refresh.py \\
        --cert pass_cert.pem --key pass_key.pem --wwdr AppleWWDRCA.pem \\
        --team-id XXXXXXXXXX --pass-type-id pass.fr.izly.paiement

Interruption propre : Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Modules locaux
import fetch_qr as _fq
import create_pass as _cp

# Format de date retourné par l'API Izly : "09/06/2026 09:21:00"
_IZLY_DATE_FMT = "%d/%m/%Y %H:%M:%S"

# Marge par défaut avant expiration pour déclencher le renouvellement
_DEFAULT_MARGIN_SEC = 90   # renouvelle 90 s avant expiration


def _parse_validity(raw: str | None) -> datetime | None:
    """
    Convertit la chaîne de validité Izly en datetime UTC naïf.
    Supporte les formats :
      - "09/06/2026 09:21:00"           (date seule, heure locale France)
      - "2026-06-09T09:21:00"           (ISO 8601)
      - "2026-06-09T09:21:00.000Z"      (ISO 8601 avec Z)
    """
    if not raw:
        return None

    # Nettoie les espaces et le suffixe Z éventuel
    raw = raw.strip()

    for fmt in (
        _IZLY_DATE_FMT,
        "%d/%m/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue

    return None


def _seconds_until(expiry: datetime) -> float:
    """Durée en secondes jusqu'à expiry (datetime naïf = heure locale)."""
    now = datetime.now()
    return (expiry - now).total_seconds()


def refresh_once(
    cookies: str,
    qr_png: str,
    pkpass: str,
    team_id: str,
    pass_type_id: str,
    cert: str | None,
    key: str | None,
    wwdr: str | None,
) -> tuple[str | None, float | None]:
    """
    Effectue un cycle complet de renouvellement.
    Retourne (validity_date_str, seconds_remaining).
    """
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Renouvellement du QR code…")

    data = _fq.fetch_qr_data(cookies)
    validity_str = _fq.extract_image(data, qr_png)

    print(f"  QR sauvegardé  : {qr_png}")
    if validity_str:
        print(f"  Valide jusqu'à : {validity_str}")

    _cp.build_pkpass(
        qr_png=qr_png,
        output=pkpass,
        team_id=team_id,
        pass_type_id=pass_type_id,
        validity_date=validity_str,
        cert=cert,
        key=key,
        wwdr=wwdr,
    )

    expiry = _parse_validity(validity_str)
    remaining = _seconds_until(expiry) if expiry else None
    return validity_str, remaining


def run_loop(
    cookies: str,
    qr_png: str,
    pkpass: str,
    team_id: str,
    pass_type_id: str,
    margin: int,
    cert: str | None,
    key: str | None,
    wwdr: str | None,
    max_cycles: int | None,
) -> None:
    """Boucle principale de renouvellement automatique."""
    cycle = 0

    while True:
        cycle += 1
        if max_cycles and cycle > max_cycles:
            print(f"\nNombre maximum de cycles atteint ({max_cycles}). Arrêt.")
            break

        validity_str, remaining = refresh_once(
            cookies=cookies,
            qr_png=qr_png,
            pkpass=pkpass,
            team_id=team_id,
            pass_type_id=pass_type_id,
            cert=cert,
            key=key,
            wwdr=wwdr,
        )

        if remaining is None:
            # Validité inconnue → on utilise 10 minutes par défaut
            sleep_sec = max(0, 10 * 60 - margin)
            print(
                f"  Durée inconnue : attente de {sleep_sec}s "
                f"(10 min − {margin}s marge)"
            )
        elif remaining <= 0:
            # QR déjà expiré (horloge désynchronisée ?) → renouvelle immédiatement
            print("  QR déjà expiré — renouvellement immédiat.")
            sleep_sec = 0
        else:
            sleep_sec = max(0, remaining - margin)
            print(
                f"  Prochain renouvellement dans {sleep_sec:.0f}s "
                f"(expiry − {margin}s marge)"
            )

        if sleep_sec > 0:
            try:
                time.sleep(sleep_sec)
            except KeyboardInterrupt:
                print("\nArrêté par l'utilisateur.")
                sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Renouvelle le QR code Izly automatiquement avant expiration."
    )
    # Chemins
    parser.add_argument("--cookies", default="cookies.txt", metavar="FILE",
                        help="Fichier cookies Netscape (défaut : cookies.txt)")
    parser.add_argument("--qr-png", default="izly_qr.png", metavar="FILE",
                        help="Image QR PNG de sortie (défaut : izly_qr.png)")
    parser.add_argument("--pkpass", default="izly.pkpass", metavar="FILE",
                        help="Pass .pkpass de sortie (défaut : izly.pkpass)")
    # Temporisation
    parser.add_argument("--margin", type=int, default=_DEFAULT_MARGIN_SEC, metavar="SEC",
                        help=f"Secondes avant expiration pour déclencher le renouvellement "
                             f"(défaut : {_DEFAULT_MARGIN_SEC})")
    parser.add_argument("--max-cycles", type=int, default=None, metavar="N",
                        help="Nombre maximum de cycles (défaut : infini)")
    # Apple Wallet
    parser.add_argument("--team-id", default="XXXXXXXXXX", metavar="ID",
                        help="Apple Team ID")
    parser.add_argument("--pass-type-id", default="pass.fr.izly.paiement", metavar="ID",
                        help="Pass Type Identifier Apple")
    parser.add_argument("--cert", default=None, metavar="FILE")
    parser.add_argument("--key",  default=None, metavar="FILE")
    parser.add_argument("--wwdr", default=None, metavar="FILE")
    args = parser.parse_args()

    if not Path(args.cookies).exists():
        sys.exit(
            f"Fichier cookies introuvable : {args.cookies!r}\n"
            "Connecte-toi à https://mon-espace.izly.fr et exporte les cookies."
        )

    # Crée le dossier de sortie si nécessaire
    for p in (args.qr_png, args.pkpass):
        parent = Path(p).parent
        if not parent.exists():
            parent.mkdir(parents=True)

    print("Izly QR Auto-Refresh")
    print(f"  Cookies  : {args.cookies}")
    print(f"  QR PNG   : {args.qr_png}")
    print(f"  .pkpass  : {args.pkpass}")
    print(f"  Marge    : {args.margin}s avant expiration")
    print("Appuie sur Ctrl+C pour arrêter.\n")

    try:
        run_loop(
            cookies=args.cookies,
            qr_png=args.qr_png,
            pkpass=args.pkpass,
            team_id=args.team_id,
            pass_type_id=args.pass_type_id,
            margin=args.margin,
            cert=args.cert,
            key=args.key,
            wwdr=args.wwdr,
            max_cycles=args.max_cycles,
        )
    except KeyboardInterrupt:
        print("\nArrêté par l'utilisateur.")


if __name__ == "__main__":
    main()
