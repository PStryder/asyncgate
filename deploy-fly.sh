#!/bin/bash
set -e

echo "=== AsyncGate Fly.io Deployment ==="

# Check if fly CLI is installed
if ! command -v fly &> /dev/null; then
    echo "Error: fly CLI not installed. Install from https://fly.io/docs/hands-on/install-flyctl/"
    exit 1
fi

# Check if logged in
if ! fly auth whoami &> /dev/null; then
    echo "Error: Not logged in to Fly.io. Run 'fly auth login'"
    exit 1
fi

# Create app if it doesn't exist
if ! fly apps list | grep -q "asyncgate"; then
    echo "Creating Fly.io app..."
    fly apps create asyncgate --org personal
fi

# Create or attach Postgres
if ! fly postgres list | grep -q "asyncgate-db"; then
    echo "Creating Postgres database..."
    fly postgres create --name asyncgate-db --region iad --initial-cluster-size 1
    fly postgres attach asyncgate-db --app asyncgate
else
    echo "Postgres database already exists"
fi

# Set secrets (only if not already set)
echo "Setting secrets..."
read -sp "Enter ASYNCGATE_API_KEY (or press enter to skip): " API_KEY
echo
if [ -n "$API_KEY" ]; then
    fly secrets set ASYNCGATE_API_KEY="$API_KEY" --app asyncgate
fi

# Deploy
echo "Deploying to Fly.io..."
fly deploy --ha=false

# Show status
echo ""
echo "=== Deployment Complete ==="
fly status --app asyncgate
echo ""
echo "App URL: https://asyncgate.fly.dev"
echo ""
echo "Useful commands:"
echo "  fly logs --app asyncgate"
echo "  fly ssh console --app asyncgate"
echo "  fly scale count 2 --app asyncgate"
