# AsyncGate Deployment Checklist

## Pre-Deployment

- [ ] All tests passing locally
- [ ] Environment variables documented
- [ ] Secrets rotation policy defined
- [ ] Backup strategy planned
- [ ] Monitoring alerts configured

## Fly.io Deployment

- [ ] Fly CLI installed and authenticated
- [ ] App created: `fly apps create asyncgate`
- [ ] Postgres created: `fly postgres create asyncgate-db`
- [ ] Database attached: `fly postgres attach`
- [ ] API key set: `fly secrets set ASYNCGATE_API_KEY=...`
- [ ] Deploy: `./deploy-fly.sh`
- [ ] Smoke tests pass: `./test_deployment.sh`
- [ ] Logs clean: `fly logs`
- [ ] Health check passing: Check dashboard

## Kubernetes Deployment

- [ ] Cluster access configured
- [ ] Namespace created
- [ ] Secrets created from secret.yaml
- [ ] Apply: `kubectl apply -k k8s/overlays/prod`
- [ ] Pods running: `kubectl get pods -n asyncgate`
- [ ] Ingress configured with domain
- [ ] TLS certificate issued
- [ ] Smoke tests pass
- [ ] HPA configured correctly

## Post-Deployment

- [ ] Monitor error rates for 24 hours
- [ ] Verify database migrations applied
- [ ] Test worker can lease tasks
- [ ] Verify receipt creation
- [ ] Document any issues encountered
- [ ] Update runbook if needed

## Rollback Plan

If deployment fails:
- Fly.io: `fly releases rollback <version>`
- K8s: `kubectl rollout undo deployment/asyncgate -n asyncgate`
- Docker: `docker-compose down && git checkout <previous-commit> && docker-compose up -d`
