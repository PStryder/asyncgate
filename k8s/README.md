# AsyncGate Kubernetes Deployment

## Prerequisites

- Kubernetes cluster (1.24+)
- kubectl configured
- kustomize (or kubectl 1.14+ with built-in kustomize)
- cert-manager (for TLS)
- nginx-ingress-controller

## Quick Start

### 1. Create namespace
```bash
kubectl apply -f base/namespace.yaml
```

### 2. Create secrets
```bash
# Copy example and edit
cp base/secret.yaml.example base/secret.yaml
# Edit with your actual secrets

# Apply
kubectl apply -f base/secret.yaml
```

### 3. Deploy to dev
```bash
kubectl apply -k overlays/dev
```

### 4. Deploy to prod
```bash
kubectl apply -k overlays/prod
```

## Verify Deployment
```bash
# Check pods
kubectl get pods -n asyncgate

# Check services
kubectl get svc -n asyncgate

# Check logs
kubectl logs -n asyncgate -l app=asyncgate --tail=100

# Port forward for testing
kubectl port-forward -n asyncgate svc/asyncgate 8080:80
```

## Scaling
```bash
# Manual scaling
kubectl scale deployment asyncgate -n asyncgate --replicas=5

# HPA will auto-scale between min/max replicas
kubectl get hpa -n asyncgate
```

## Updating
```bash
# Update image
kubectl set image deployment/asyncgate -n asyncgate asyncgate=asyncgate:v0.3.0

# Or apply updated manifests
kubectl apply -k overlays/prod
```
