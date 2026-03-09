# Mise en ligne (Render)

## 1. Préparer le dépôt
- Placez ce projet dans un dépôt GitHub.
- Vérifiez que les fichiers suivants sont bien présents:
  - `requirements.txt`
  - `render.yaml`
  - `.env.example`

## 2. Déploiement automatique avec Blueprint
- Sur Render, cliquez **New +** > **Blueprint**.
- Connectez le dépôt GitHub.
- Render détecte automatiquement `render.yaml` et crée le service web.

## 3. Variables d'environnement
Variables déjà prévues dans `render.yaml`:
- `FLASK_SECRET_KEY` (générée automatiquement)
- `SESSION_COOKIE_SECURE=1`
- `TRUST_PROXY=1`
- `MAX_CV_FILE_SIZE_MB=10`
- `SVH_DATA_DIR=/var/data`

## 4. Stockage persistant
- Le disque Render (`/var/data`) est configuré pour conserver:
  - candidatures freelance
  - CV déposés
  - leads premium

## 5. Vérification après déploiement
- URL santé: `/healthz` doit répondre `{"status":"ok"}`.
- Vérifiez les pages:
  - `/`
  - `/qui-sommes-nous`
  - `/formations`
  - `/remplacements`
  - `/ressources`
  - `/contact-et-infos`

## 6. Domaine personnalisé
- Dans Render > Service > **Settings** > **Custom Domains**.
- Ajoutez votre domaine puis configurez les DNS chez votre registrar.

## Note importante (formulaires email)
Les formulaires sont envoyés en backend (POST serveur) et enregistrés dans `SVH_DATA_DIR`.

Pour recevoir un email de notification à chaque envoi, configurez aussi :
- `SMTP_HOST`
- `SMTP_PORT` (587 par défaut)
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_USE_TLS` (`1` recommandé)
- `SMTP_USE_SSL` (`0` si TLS activé)
- `CONTACT_EMAIL_TO` (destinataire des notifications)
- `CONTACT_EMAIL_FROM` (expéditeur affiché)

Option :
- `EMAIL_NOTIFICATIONS_REQUIRED=1` pour considérer l’envoi comme en échec si la notification email SMTP ne part pas.
