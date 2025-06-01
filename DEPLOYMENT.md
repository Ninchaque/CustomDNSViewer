# Déploiement DNS Manager sur K3s

Ce guide explique comment déployer l'interface de gestion DNS sur votre cluster K3s en utilisant Helm.

## 📋 Prérequis

- **K3s** installé et fonctionnel
- **Docker** pour construire l'image
- **Helm 3.x** installé
- **kubectl** configuré pour votre cluster K3s
- **Traefik** installé (généralement inclus avec K3s)

## 🏗️ Architecture de déploiement

```
Internet → Traefik Ingress → Service → Pod (DNS Manager)
                          ↓
                      ConfigMap + Secret
                          ↓
                    PersistentVolume (optionnel)
```

## 📁 Structure des fichiers

```
├── Dockerfile                     # Image Docker de l'application
├── .dockerignore                  # Fichiers exclus du build Docker
├── deploy.sh                      # Script de déploiement automatisé
├── helm-chart/                    # Chart Helm
│   ├── Chart.yaml                 # Métadonnées du chart
│   ├── values.yaml                # Valeurs configurables
│   └── templates/
│       ├── _helpers.tpl           # Fonctions utilitaires
│       ├── deployment.yaml        # Déploiement Kubernetes
│       ├── service.yaml           # Service d'exposition
│       ├── ingress.yaml           # Ingress pour l'accès externe
│       ├── configmap.yaml         # Configuration non sensible
│       ├── secret.yaml            # Données sensibles
│       ├── serviceaccount.yaml    # Compte de service
│       └── pvc.yaml               # Volume persistant (optionnel)
└── DEPLOYMENT.md                  # Ce fichier
```

## 🚀 Déploiement rapide

### Option 1: Script automatisé (recommandé)

```bash
# Rendre le script exécutable
chmod +x deploy.sh

# Construire l'image et déployer
./deploy.sh --all
```

### Option 2: Étapes manuelles

```bash
# 1. Construire l'image Docker
docker build -t dns-manager:latest .

# 2. Créer le namespace
kubectl create namespace dns-manager

# 3. Déployer avec Helm
helm install dns-manager ./helm-chart \
    --namespace dns-manager \
    --set image.repository=dns-manager \
    --set image.tag=latest
```

## ⚙️ Configuration

### Variables principales (values.yaml)

```yaml
# Configuration de l'image
image:
  repository: dns-manager
  tag: latest

# Configuration DNS
app:
  dnsServer: "192.168.1.201"  # IP de votre serveur DNS BIND
  
# Configuration d'accès
ingress:
  hosts:
    - host: dns.solal.internal  # Votre domaine
```

### Personnalisation

Pour personnaliser la configuration, vous pouvez :

1. **Modifier values.yaml** directement
2. **Utiliser des paramètres Helm** :
   ```bash
   helm install dns-manager ./helm-chart \
       --set app.dnsServer="votre-ip-dns" \
       --set ingress.hosts[0].host="votre-domaine.local"
   ```

## 🔧 Commandes utiles

### Script de déploiement

```bash
# Construire l'image seulement
./deploy.sh --build

# Déployer seulement (image existante)
./deploy.sh --deploy

# Mettre à jour une installation existante
./deploy.sh --upgrade

# Supprimer complètement
./deploy.sh --remove

# Aide
./deploy.sh --help
```

### Commandes Helm directes

```bash
# Lister les déploiements
helm list -n dns-manager

# Voir le statut
helm status dns-manager -n dns-manager

# Mettre à jour
helm upgrade dns-manager ./helm-chart -n dns-manager

# Supprimer
helm uninstall dns-manager -n dns-manager
```

### Commandes Kubernetes

```bash
# Voir les pods
kubectl get pods -n dns-manager

# Voir les logs
kubectl logs -f deployment/dns-manager -n dns-manager

# Voir les services et ingress
kubectl get svc,ingress -n dns-manager

# Accéder au pod (debug)
kubectl exec -it deployment/dns-manager -n dns-manager -- /bin/bash
```

## 🌐 Accès à l'application

Une fois déployé, l'application sera accessible à :
- **URL** : `https://dns.solal.internal`
- **Port** : 443 (HTTPS via Traefik)

### Configuration DNS locale

Ajoutez cette ligne à votre `/etc/hosts` si vous n'avez pas de DNS local :
```
<IP-DE-VOTRE-K3S> dns.solal.internal
```

## 🔒 Sécurité

### Données sensibles
- La clé secrète Flask est stockée dans un **Secret Kubernetes**
- Les configurations SSH sont gérées via l'interface web (session)

### Recommandations
1. **Changez la clé secrète** en production :
   ```yaml
   app:
     flask:
       secretKey: "votre-cle-secrete-forte"
   ```

2. **Activez TLS** avec cert-manager :
   ```yaml
   ingress:
     annotations:
       cert-manager.io/cluster-issuer: "letsencrypt-prod"
   ```

## 📊 Monitoring et logs

### Voir les logs
```bash
# Logs de l'application
kubectl logs -f -l app.kubernetes.io/name=dns-manager -n dns-manager

# Logs Traefik (si problème d'ingress)
kubectl logs -f -l app.kubernetes.io/name=traefik -n kube-system
```

### Ressources
Les ressources par défaut :
- **CPU** : 100m (request) / 500m (limit)
- **Mémoire** : 128Mi (request) / 512Mi (limit)

## 🔧 Troubleshooting

### Problèmes courants

1. **Image non trouvée** :
   ```bash
   # Vérifier que l'image existe
   docker images | grep dns-manager
   
   # Reconstruire si nécessaire
   ./deploy.sh --build
   ```

2. **Ingress ne fonctionne pas** :
   ```bash
   # Vérifier Traefik
   kubectl get pods -n kube-system | grep traefik
   
   # Vérifier l'ingress
   kubectl describe ingress dns-manager -n dns-manager
   ```

3. **Pod ne démarre pas** :
   ```bash
   # Voir les événements
   kubectl describe pod -l app.kubernetes.io/name=dns-manager -n dns-manager
   
   # Voir les logs
   kubectl logs -l app.kubernetes.io/name=dns-manager -n dns-manager
   ```

### Debug mode

Pour activer le debug :
```bash
helm upgrade dns-manager ./helm-chart \
    --set app.flask.debug=true \
    --set app.logging.level=DEBUG \
    -n dns-manager
```

## 🔄 Mise à jour

### Code de l'application
```bash
# 1. Reconstruire l'image
./deploy.sh --build

# 2. Mettre à jour le déploiement
./deploy.sh --upgrade
```

### Configuration seulement
```bash
# Modifier values.yaml puis :
helm upgrade dns-manager ./helm-chart -n dns-manager
```

## 📝 Variables d'environnement

L'application utilise ces variables :
- `DNS_SERVER` : IP du serveur DNS BIND
- `SECRET_KEY` : Clé secrète Flask
- `FLASK_ENV` : Environnement (production/development)
- `PYTHONUNBUFFERED` : Affichage des logs en temps réel

## 🎯 Prochaines étapes

1. **Configurer SSH** via l'interface web
2. **Tester l'ajout/modification** d'enregistrements DNS
3. **Configurer la sauvegarde** des configurations SSH
4. **Monitorer les performances** de l'application 