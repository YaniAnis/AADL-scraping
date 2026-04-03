"""
╔══════════════════════════════════════════════════════════════╗
║       VEILLE IMMOBILIÈRE ALGÉRIE - Notification Email        ║
║     AADL (3 pages) + ENPI — Selenium + Requests              ║
╚══════════════════════════════════════════════════════════════╝
 
INSTALLATION :
    pip install selenium beautifulsoup4 lxml webdriver-manager requests python-dotenv
 
    Selenium simule un vrai navigateur Chrome pour contourner
    le blocage anti-scraping de AADL (erreur 403).
    ENPI est accessible normalement via requests.
 
PRÉREQUIS :
    - Google Chrome installé sur votre machine
    - Le script télécharge ChromeDriver automatiquement
 
CONFIGURATION :
    1. Copiez .env.example en .env et remplissez vos identifiants
    2. Pour Gmail : Mon compte Google > Sécurité >
       Authentification 2 facteurs > Mots de passe d'application
    3. Lancez : python veille_immobilier_dz.py
 
AUTOMATISATION (Linux/Mac) — crontab -e :
    0 */6 * * * python3 /chemin/vers/veille_immobilier_dz.py
 
AUTOMATISATION (Windows) :
    Utilisez le Planificateur de tâches Windows.
"""
 
import smtplib
import json
import hashlib
import os
import sys
import logging
import time
import requests
import urllib3
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin
from bs4 import BeautifulSoup
 
# python-dotenv — charge les variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Si python-dotenv n'est pas installé, on utilise les variables système

# Selenium — navigateur Chrome headless
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
 
# Téléchargement automatique du ChromeDriver
try:
    from webdriver_manager.chrome import ChromeDriverManager
    WEBDRIVER_MANAGER = True
except ImportError:
    WEBDRIVER_MANAGER = False
 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
 
# ─────────────────────────────────────────────
#  ⚙️  CONFIGURATION — Chargée depuis .env
# ─────────────────────────────────────────────

def _charger_config_email():
    """Charge la configuration email depuis les variables d'environnement (.env)."""
    config = {
        "expediteur":   os.getenv("EMAIL_EXPEDITEUR", ""),
        "mot_de_passe": os.getenv("EMAIL_MOT_DE_PASSE", ""),
        "destinataire": os.getenv("EMAIL_DESTINATAIRE", ""),
        "smtp_server":  os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        "smtp_port":    int(os.getenv("SMTP_PORT", "587")),
    }
    # Validation au démarrage
    champs_requis = ["expediteur", "mot_de_passe", "destinataire"]
    manquants = [c for c in champs_requis if not config[c]]
    if manquants:
        print("=" * 60)
        print("❌ ERREUR DE CONFIGURATION")
        print("=" * 60)
        print(f"   Variable(s) manquante(s) dans .env : {', '.join(manquants)}")
        print()
        print("   → Copiez .env.example en .env et remplissez vos identifiants :")
        print("     cp .env.example .env")
        print("=" * 60)
        sys.exit(1)
    return config


EMAIL_CONFIG = _charger_config_email()
 
# Fichier mémoire des annonces déjà vues
FICHIER_HISTORIQUE = "annonces_vues.json"

# Durée de rétention des entrées dans l'historique (en jours)
RETENTION_HISTORIQUE_JOURS = 90
 
# ─────────────────────────────────────────────
#  🌐  SITES SURVEILLÉS
# ─────────────────────────────────────────────
 
# Pages AADL — scraping via Selenium (navigateur réel)
SITES_SELENIUM = [
    {
        "nom": "AADL — Adjudications",
        "url": "https://www.aadl.com.dz/locaux/programme_lgg/production/pagewilcom_adjudication.php",
        "url_base": "https://www.aadl.com.dz",
    },
    {
        "nom": "AADL — Locaux LGG",
        "url": "https://www.aadl.com.dz/locaux/programme_lgg/production/pagewilcom_lgg.php",
        "url_base": "https://www.aadl.com.dz",
    },
    {
        "nom": "AADL — Locaux TER1",
        "url": "https://www.aadl.com.dz/locaux/programme_lgg/production/pagewilcom_ter1.php",
        "url_base": "https://www.aadl.com.dz",
    },
]
 
# Pages ENPI — scraping via requests classique
SITES_REQUESTS = [
    {
        "nom": "ENPI — Adjudications",
        "url": "https://www.enpi-net.dz/LocauxEnpi/Adjudication.php",
        "url_base": "https://www.enpi-net.dz",
        "ssl_verify": False,
    },
]
 
# ─────────────────────────────────────────────
#  🔧  LOGGING & UTILITAIRES
# ─────────────────────────────────────────────
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("veille.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
 
HEADERS_REQUESTS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
 
 
def charger_historique():
    """Charge l'historique depuis le fichier JSON, avec pruning automatique."""
    if os.path.exists(FICHIER_HISTORIQUE):
        try:
            with open(FICHIER_HISTORIQUE, "r", encoding="utf-8") as f:
                historique = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"⚠️  Historique corrompu, remise à zéro : {e}")
            return {}

        # Pruning : supprimer les entrées plus vieilles que RETENTION_HISTORIQUE_JOURS
        seuil = datetime.now() - timedelta(days=RETENTION_HISTORIQUE_JOURS)
        avant = len(historique)
        historique_filtre = {}
        for cle, val in historique.items():
            date_str = val.get("date", "")
            try:
                date_entry = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
                if date_entry >= seuil:
                    historique_filtre[cle] = val
            except (ValueError, TypeError):
                # Garder les entrées sans date valide (rétrocompatibilité)
                historique_filtre[cle] = val

        supprimees = avant - len(historique_filtre)
        if supprimees > 0:
            log.info(f"🧹 Historique purgé : {supprimees} entrée(s) de plus de {RETENTION_HISTORIQUE_JOURS} jours supprimée(s)")

        return historique_filtre
    return {}
 
 
def sauvegarder_historique(historique):
    """Sauvegarde l'historique dans le fichier JSON de manière atomique."""
    fichier_tmp = FICHIER_HISTORIQUE + ".tmp"
    try:
        with open(fichier_tmp, "w", encoding="utf-8") as f:
            json.dump(historique, f, ensure_ascii=False, indent=2)
        # Remplacement atomique (évite la corruption si le script est interrompu)
        if os.path.exists(FICHIER_HISTORIQUE):
            os.replace(fichier_tmp, FICHIER_HISTORIQUE)
        else:
            os.rename(fichier_tmp, FICHIER_HISTORIQUE)
    except IOError as e:
        log.error(f"❌ Erreur sauvegarde historique : {e}")
        # Nettoyer le fichier temporaire en cas d'erreur
        if os.path.exists(fichier_tmp):
            os.remove(fichier_tmp)
 
 
def generer_id(texte):
    """Génère un identifiant unique pour une annonce (SHA-256)."""
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()
 
 
def creer_driver_chrome():
    """Crée un driver Chrome headless (invisible) avec anti-détection."""
    options = Options()
    options.add_argument("--headless=new")           # Invisible
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fr-FR")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
 
    if WEBDRIVER_MANAGER:
        service = Service(ChromeDriverManager().install())
    else:
        service = Service()
    driver = webdriver.Chrome(service=service, options=options)
 
    # Masquer la signature Selenium via CDP (avant tout chargement de page)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver
 
 
def construire_lien(href, url_base):
    """Construit une URL absolue à partir d'un href et d'une URL de base."""
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return urljoin(url_base + "/", href)


def parser_tableau(html, url_base, nom_site):
    """
    Parse le HTML et extrait les lignes de tableaux.
    Retourne une liste de dicts {titre, lien, source, date_detection}.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
 
    annonces = []
    tables = soup.find_all("table")
 
    if not tables:
        # Fallback : blocs texte
        for el in soup.find_all(["p", "li", "div"]):
            texte = el.get_text(strip=True)
            if len(texte) > 30:
                lien_el = el.find("a")
                lien = ""
                if lien_el and lien_el.get("href"):
                    lien = construire_lien(lien_el["href"], url_base)
                annonces.append({
                    "titre": texte[:300],
                    "lien": lien,
                    "source": nom_site,
                    "date_detection": datetime.now().strftime("%d/%m/%Y %H:%M"),
                })
        return annonces
 
    for table in tables:
        # En-têtes du tableau
        headers = []
        header_row = table.find("tr")
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
 
        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            valeurs = [c.get_text(strip=True) for c in cells if c.get_text(strip=True)]
            if not valeurs:
                continue
 
            if headers and len(headers) >= len(valeurs):
                titre = " | ".join(
                    f"{headers[i]}: {valeurs[i]}"
                    for i in range(len(valeurs)) if valeurs[i]
                )
            else:
                titre = " | ".join(valeurs)
 
            if len(titre) < 5:
                continue
 
            lien_el = row.find("a")
            lien = ""
            if lien_el and lien_el.get("href"):
                lien = construire_lien(lien_el["href"], url_base)
 
            annonces.append({
                "titre": titre[:400],
                "lien": lien,
                "source": nom_site,
                "date_detection": datetime.now().strftime("%d/%m/%Y %H:%M"),
            })
 
    return annonces
 
 
# ─────────────────────────────────────────────
#  🤖  SCRAPING SELENIUM (AADL)
# ─────────────────────────────────────────────
 
def scraper_avec_selenium(sites):
    """Scrape plusieurs pages avec un seul navigateur Chrome."""
    toutes_annonces = []
    driver = None
 
    try:
        log.info("🌐 Démarrage du navigateur Chrome (headless)...")
        driver = creer_driver_chrome()
 
        for site in sites:
            try:
                log.info(f"🔍 Selenium — {site['nom']}")
                driver.get(site["url"])
 
                # Attendre que la page charge (tableaux ou body)
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.TAG_NAME, "table"))
                    )
                except Exception:
                    # Pas de tableau détecté, attendre quand même
                    time.sleep(3)
 
                html = driver.page_source
                annonces = parser_tableau(html, site["url_base"], site["nom"])
                log.info(f"   ✅ {len(annonces)} entrée(s) trouvée(s)")
                toutes_annonces.extend(annonces)
 
                time.sleep(2)  # Pause entre les pages pour éviter le blocage
 
            except Exception as e:
                log.error(f"   ❌ Erreur Selenium sur {site['nom']} : {e}")
 
    except Exception as e:
        log.error(f"❌ Impossible de démarrer Chrome : {e}")
        log.error("   → Vérifiez que Google Chrome est bien installé sur votre machine.")
    finally:
        if driver:
            driver.quit()
            log.info("🌐 Navigateur Chrome fermé.")
 
    return toutes_annonces
 
 
# ─────────────────────────────────────────────
#  🔗  SCRAPING REQUESTS (ENPI)
# ─────────────────────────────────────────────
 
def scraper_avec_requests(sites):
    """Scrape les sites accessibles normalement via requests."""
    toutes_annonces = []
 
    for site in sites:
        try:
            log.info(f"🔍 Requests — {site['nom']}")
            verify = site.get("ssl_verify", True)
 
            resp = requests.get(
                site["url"],
                headers=HEADERS_REQUESTS,
                timeout=20,
                verify=verify,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
 
            annonces = parser_tableau(resp.text, site["url_base"], site["nom"])
            log.info(f"   ✅ {len(annonces)} entrée(s) trouvée(s)")
            toutes_annonces.extend(annonces)
 
        except requests.exceptions.ConnectionError:
            log.warning(f"   ⚠️  Impossible de joindre {site['nom']}")
        except requests.exceptions.Timeout:
            log.warning(f"   ⚠️  Timeout sur {site['nom']}")
        except requests.exceptions.HTTPError as e:
            log.warning(f"   ⚠️  HTTP {e.response.status_code} sur {site['nom']}")
        except Exception as e:
            log.error(f"   ❌ Erreur sur {site['nom']} : {e}")
 
    return toutes_annonces
 
 
# ─────────────────────────────────────────────
#  📧  EMAIL HTML
# ─────────────────────────────────────────────
 
def construire_email_html(nouvelles_annonces):
    par_source = {}
    for a in nouvelles_annonces:
        par_source.setdefault(a["source"], []).append(a)
 
    sections_html = ""
    for source, annonces in par_source.items():
        lignes = ""
        for a in annonces:
            lien_html = (
                f'<a href="{a["lien"]}" style="color:#2980b9;">🔗 Voir</a>'
                if a["lien"] else "<span style='color:#aaa;'>—</span>"
            )
            lignes += f"""
            <tr>
                <td style="padding:10px 8px; border-bottom:1px solid #f0f0f0;
                           font-size:13px; color:#2c3e50;">{a['titre']}</td>
                <td style="padding:10px 8px; border-bottom:1px solid #f0f0f0;
                           font-size:12px; color:#7f8c8d; white-space:nowrap;">{a['date_detection']}</td>
                <td style="padding:10px 8px; border-bottom:1px solid #f0f0f0;
                           white-space:nowrap;">{lien_html}</td>
            </tr>"""
 
        sections_html += f"""
        <div style="margin-bottom:25px;">
            <h3 style="margin:0 0 10px; color:#1a5276; border-left:4px solid #2980b9;
                       padding-left:10px; font-size:15px;">🏢 {source}</h3>
            <table style="width:100%; border-collapse:collapse; background:#fafafa; border-radius:6px;">
                <thead>
                    <tr style="background:#2980b9; color:white; font-size:12px;">
                        <th style="padding:8px; text-align:left;">Détail</th>
                        <th style="padding:8px; text-align:left;">Détecté le</th>
                        <th style="padding:8px; text-align:left;">Lien</th>
                    </tr>
                </thead>
                <tbody>{lignes}</tbody>
            </table>
        </div>"""
 
    return f"""
    <html><body style="font-family:Arial,sans-serif; background:#f0f3f4; padding:20px; margin:0;">
    <div style="max-width:750px; margin:auto; background:white; border-radius:10px;
                box-shadow:0 2px 12px rgba(0,0,0,0.1); overflow:hidden;">
        <div style="background:linear-gradient(135deg,#1a5276,#2980b9); color:white; padding:25px;">
            <h1 style="margin:0; font-size:22px;">🏢 Nouvelles Annonces Immobilières</h1>
            <p style="margin:6px 0 0; opacity:0.85; font-size:14px;">
                AADL · ENPI &nbsp;|&nbsp; {datetime.now().strftime('%d/%m/%Y à %H:%M')}
            </p>
        </div>
        <div style="padding:25px;">
            <p style="color:#555; margin:0 0 20px;">
                <strong style="color:#e74c3c;">{len(nouvelles_annonces)}</strong>
                nouvelle(s) entrée(s) détectée(s) :
            </p>
            {sections_html}
            <div style="padding:12px 15px; background:#eaf4fb; border-left:4px solid #2980b9;
                        border-radius:4px; font-size:12px; color:#666; margin-top:10px;">
                ℹ️ Message automatique — Veille immobilière Algérie (AADL + ENPI)
            </div>
        </div>
    </div>
    </body></html>"""
 
 
def envoyer_email(nouvelles_annonces, max_tentatives=3):
    """Envoie l'email d'alerte avec retry automatique en cas d'erreur transitoire."""
    if not nouvelles_annonces:
        return
 
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🏢 [{len(nouvelles_annonces)} entrée(s)] AADL / ENPI — Nouvelles annonces"
    msg["From"]    = EMAIL_CONFIG["expediteur"]
    msg["To"]      = EMAIL_CONFIG["destinataire"]
 
    texte = f"Nouvelles annonces — {datetime.now().strftime('%d/%m/%Y %H:%M')} :\n\n"
    for a in nouvelles_annonces:
        texte += f"[{a['source']}]\n{a['titre']}\nLien : {a['lien'] or '—'}\n\n"
 
    msg.attach(MIMEText(texte, "plain", "utf-8"))
    msg.attach(MIMEText(construire_email_html(nouvelles_annonces), "html", "utf-8"))
 
    for tentative in range(1, max_tentatives + 1):
        try:
            with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as srv:
                srv.starttls()
                srv.login(EMAIL_CONFIG["expediteur"], EMAIL_CONFIG["mot_de_passe"])
                srv.sendmail(EMAIL_CONFIG["expediteur"], EMAIL_CONFIG["destinataire"], msg.as_string())
            log.info(f"📧 Email envoyé avec {len(nouvelles_annonces)} nouvelle(s) entrée(s) !")
            return  # Succès, on sort
        except smtplib.SMTPAuthenticationError:
            log.error("❌ Erreur Gmail : vérifiez votre mot de passe d'application (16 caractères).")
            return  # Pas de retry pour les erreurs d'authentification
        except Exception as e:
            log.warning(f"   ⚠️  Tentative {tentative}/{max_tentatives} échouée : {e}")
            if tentative < max_tentatives:
                delai = tentative * 5  # Backoff : 5s, 10s, 15s
                log.info(f"   ⏳ Nouvelle tentative dans {delai}s...")
                time.sleep(delai)
            else:
                log.error(f"❌ Échec définitif de l'envoi email après {max_tentatives} tentatives.")
 
 
# ─────────────────────────────────────────────
#  🚀  PROGRAMME PRINCIPAL
# ─────────────────────────────────────────────
 
def lancer_veille():
    log.info("=" * 60)
    log.info("🚀 Démarrage de la veille — AADL + ENPI")
    log.info(f"   Date : {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    log.info("=" * 60)
 
    historique = charger_historique()
    toutes_annonces = []
 
    # 1. AADL via Selenium
    toutes_annonces += scraper_avec_selenium(SITES_SELENIUM)
 
    # 2. ENPI via requests
    toutes_annonces += scraper_avec_requests(SITES_REQUESTS)
 
    # 3. Filtrer les nouvelles annonces
    nouvelles_annonces = []
    for annonce in toutes_annonces:
        annonce_id = generer_id(annonce["titre"] + annonce["source"])
        if annonce_id not in historique:
            historique[annonce_id] = {
                "titre": annonce["titre"][:100],
                "source": annonce["source"],
                "date": annonce["date_detection"],
            }
            nouvelles_annonces.append(annonce)
            log.info(f"   🆕 NOUVEAU : {annonce['titre'][:70]}...")
 
    sauvegarder_historique(historique)
 
    # 4. Envoyer l'email si nouvelles entrées
    if nouvelles_annonces:
        log.info(f"\n📬 {len(nouvelles_annonces)} nouvelle(s) → envoi email...")
        envoyer_email(nouvelles_annonces)
    else:
        log.info("\n✅ Aucune nouvelle entrée. Pas d'email envoyé.")
 
    log.info("=" * 60)
    log.info("✔️  Veille terminée.")
    log.info("=" * 60)
 
 
if __name__ == "__main__":
    lancer_veille()