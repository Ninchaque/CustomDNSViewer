#!/bin/bash

# Script de déploiement pour DNS Manager sur K3s
set -e


NAMESPACE="dns-manager"
RELEASE_NAME="dns-manager"
IMAGE_NAME="dns-manager"
IMAGE_TAG="latest"
CHART_PATH="./helm-chart"

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Fonction d'affichage
log() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Fonction d'aide
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  -b, --build     Construire l'image Docker"
    echo "  -d, --deploy    Déployer sur K3s"
    echo "  -u, --upgrade   Mettre à jour le déploiement"
    echo "  -r, --remove    Supprimer le déploiement"
    echo "  -a, --all       Construire et déployer"
    echo "  -h, --help      Afficher cette aide"
    echo ""
    echo "Exemples:"
    echo "  $0 --build      # Construire l'image"
    echo "  $0 --deploy     # Déployer sur K3s"
    echo "  $0 --all        # Construire et déployer"
}

# Fonction de build Docker
build_image() {
    log "Construction de l'image Docker..."
    
    # Vérifier que Docker est disponible
    if ! command -v docker &> /dev/null; then
        error "Docker n'est pas installé ou accessible"
        exit 1
    fi
    
    # Construire l'image
    docker build -t $IMAGE_NAME:$IMAGE_TAG .
    
    if [ $? -eq 0 ]; then
        success "Image Docker construite: $IMAGE_NAME:$IMAGE_TAG"
    else
        error "Échec de la construction de l'image Docker"
        exit 1
    fi
}

# Fonction de déploiement
deploy() {
    log "Déploiement sur K3s..."
    
    # Vérifier que Helm est disponible
    if ! command -v helm &> /dev/null; then
        error "Helm n'est pas installé ou accessible"
        exit 1
    fi
    
    # Vérifier que kubectl est disponible
    if ! command -v kubectl &> /dev/null; then
        error "kubectl n'est pas installé ou accessible"
        exit 1
    fi
    
    # Créer le namespace s'il n'existe pas
    kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -
    
    # Déployer avec Helm
    helm install $RELEASE_NAME $CHART_PATH \
        --namespace $NAMESPACE \
        --set image.repository=$IMAGE_NAME \
        --set image.tag=$IMAGE_TAG \
        --wait
    
    if [ $? -eq 0 ]; then
        success "Déploiement réussi!"
        log "Application accessible à l'adresse: https://dns.solal.internal"
        
        # Afficher les informations de déploiement
        kubectl get pods,svc,ingress -n $NAMESPACE
    else
        error "Échec du déploiement"
        exit 1
    fi
}

# Fonction de mise à jour
upgrade() {
    log "Mise à jour du déploiement..."
    
    helm upgrade $RELEASE_NAME $CHART_PATH \
        --namespace $NAMESPACE \
        --set image.repository=$IMAGE_NAME \
        --set image.tag=$IMAGE_TAG \
        --wait
    
    if [ $? -eq 0 ]; then
        success "Mise à jour réussie!"
    else
        error "Échec de la mise à jour"
        exit 1
    fi
}

# Fonction de suppression
remove() {
    warning "Suppression du déploiement..."
    
    # Confirmer la suppression
    read -p "Êtes-vous sûr de vouloir supprimer le déploiement? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "Suppression annulée"
        exit 0
    fi
    
    # Supprimer le déploiement Helm
    helm uninstall $RELEASE_NAME --namespace $NAMESPACE
    
    # Supprimer le namespace (optionnel)
    read -p "Supprimer également le namespace '$NAMESPACE'? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        kubectl delete namespace $NAMESPACE
    fi
    
    success "Déploiement supprimé"
}

# Fonction principale
main() {
    if [ $# -eq 0 ]; then
        usage
        exit 1
    fi
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            -b|--build)
                build_image
                shift
                ;;
            -d|--deploy)
                deploy
                shift
                ;;
            -u|--upgrade)
                upgrade
                shift
                ;;
            -r|--remove)
                remove
                shift
                ;;
            -a|--all)
                build_image
                deploy
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "Option inconnue: $1"
                usage
                exit 1
                ;;
        esac
    done
}

# Vérifier les prérequis
check_prerequisites() {
    log "Vérification des prérequis..."
    
    # Vérifier la présence du chart Helm
    if [ ! -d "$CHART_PATH" ]; then
        error "Le chart Helm n'existe pas: $CHART_PATH"
        exit 1
    fi
    
    # Vérifier la connexion au cluster K3s
    if ! kubectl cluster-info &> /dev/null; then
        error "Impossible de se connecter au cluster Kubernetes"
        exit 1
    fi
    
    success "Prérequis validés"
}

# Point d'entrée
check_prerequisites
main "$@" 