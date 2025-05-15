# API Flask pour Upload de Fichiers vers Supabase Storage

Cette API Flask permet l'upload sécurisé de fichiers vers Supabase Storage. Elle est conçue pour être déployée facilement sur Render.com.

## Fonctionnalités

- Upload sécurisé de fichiers (PDF, images)
- Validation des types de fichiers et de la taille
- Stockage dans Supabase Storage
- Gestion des erreurs robuste
- Endpoint de vérification de santé

## Prérequis

- Python 3.8+
- Compte Supabase
- Compte Render.com (pour le déploiement)

## Configuration

1. Clonez ce dépôt
2. Installez les dépendances :
   ```bash
   pip install -r requirements.txt
   ```
3. Créez un fichier `.env` basé sur `.env.example` et configurez vos variables d'environnement :
   ```
   SUPABASE_URL=votre_url_supabase
   SUPABASE_KEY=votre_clé_supabase
   BUCKET_NAME=uploads
   ```

## Configuration de Supabase

1. Créez un nouveau projet sur [Supabase](https://supabase.com)
2. Dans la section Storage, créez un nouveau bucket nommé "uploads"
3. Configurez les politiques de sécurité du bucket :
   - Activez RLS (Row Level Security)
   - Créez une politique permettant l'upload de fichiers

## Déploiement sur Render.com

1. Créez un nouveau compte sur [Render.com](https://render.com)
2. Connectez votre dépôt GitHub
3. Créez un nouveau "Web Service"
4. Configurez les variables d'environnement dans l'interface Render
5. Déployez !

## Utilisation de l'API

### Upload de fichier

```bash
curl -X POST -F 'file=@mon_fichier.pdf' https://votre-api.onrender.com/upload
```

Réponse réussie :
```json
{
    "message": "Fichier uploadé avec succès",
    "filename": "mon_fichier.pdf",
    "public_url": "https://votre-projet.supabase.co/storage/v1/object/public/uploads/mon_fichier.pdf"
}
```

### Vérification de santé

```bash
curl https://votre-api.onrender.com/health
```

## Types de fichiers supportés

- PDF (.pdf)
- Images (.png, .jpg, .jpeg, .gif)

## Limites

- Taille maximale de fichier : 10MB
- Types de fichiers limités aux formats listés ci-dessus

## Sécurité

- Validation des types de fichiers
- Sécurisation des noms de fichiers
- Gestion des erreurs robuste
- Variables d'environnement pour les informations sensibles

## Développement local

1. Créez un environnement virtuel :
   ```bash
   python -m venv venv
   source venv/bin/activate  # Sur Unix/macOS
   # ou
   .\venv\Scripts\activate  # Sur Windows
   ```

2. Installez les dépendances :
   ```bash
   pip install -r requirements.txt
   ```

3. Lancez le serveur de développement :
   ```bash
   python app.py
   ```

## Tests

Pour exécuter les tests :
```bash
pytest
```

## Contribution

Les contributions sont les bienvenues ! N'hésitez pas à ouvrir une issue ou une pull request.

## Licence

MIT