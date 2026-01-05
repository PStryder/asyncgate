# Authentication Security Hardening

## Problem
The original `verify_api_key()` implementation had a critical footgun:

```python
if not settings.api_key:
    return True  # DANGEROUS: Missing key = auth disabled
```

This meant that forgetting to set `ASYNCGATE_API_KEY` in production would silently disable all authentication, leaving the API wide open.

## Solution
The hardened implementation **fails closed** by default:

### 1. Startup Validation
Server refuses to start with insecure configuration:

```python
validate_auth_config()  # Called during lifespan startup
```

**Behavior:**
- `allow_insecure_dev=true` in non-development → **RuntimeError**
- No `api_key` set outside insecure dev mode → **RuntimeError**
- Clear error messages guide proper configuration

### 2. Request-Time Enforcement
If startup validation is bypassed, requests fail with 503:

```python
if not settings.api_key:
    raise HTTPException(
        status_code=503,
        detail="Server misconfigured: authentication not properly initialized"
    )
```

### 3. Explicit Insecure Mode
Development convenience requires explicit opt-in:

```bash
# Only works in development environment
ASYNCGATE_ENV=development
ASYNCGATE_ALLOW_INSECURE_DEV=true
```

**Loud warnings on startup:**
```
================================================================================
WARNING: Running in INSECURE DEV MODE
  - Authentication is DISABLED
  - All API requests will be accepted without verification
  - This mode is ONLY for local development
  - Set ASYNCGATE_ALLOW_INSECURE_DEV=false for any deployment
================================================================================
```

## Configuration Matrix

| Environment | `api_key` | `allow_insecure_dev` | Result |
|-------------|-----------|---------------------|--------|
| development | ✗         | false               | **RuntimeError** at startup |
| development | ✗         | true                | ✓ Auth disabled (with warnings) |
| development | ✓         | false               | ✓ Auth enforced |
| development | ✓         | true                | ✓ Auth enforced (setting ignored) |
| staging     | ✗         | false               | **RuntimeError** at startup |
| staging     | ✗         | true                | **RuntimeError** at startup |
| staging     | ✓         | false               | ✓ Auth enforced |
| staging     | ✓         | true                | **RuntimeError** at startup |
| production  | ✗         | *any*               | **RuntimeError** at startup |
| production  | ✓         | false               | ✓ Auth enforced |
| production  | ✓         | true                | **RuntimeError** at startup |

## Testing

**Test insecure dev mode:**
```bash
ASYNCGATE_ENV=development \
ASYNCGATE_ALLOW_INSECURE_DEV=true \
python -m asyncgate.main
```

**Test production enforcement:**
```bash
ASYNCGATE_ENV=production \
python -m asyncgate.main
# Should fail immediately: "api_key is required in production environment"
```

**Test proper production config:**
```bash
ASYNCGATE_ENV=production \
ASYNCGATE_API_KEY=your-secure-token-here \
python -m asyncgate.main
# Should start successfully
```

## Migration Guide

### Before (Insecure)
```bash
# Missing api_key silently disabled auth
ASYNCGATE_ENV=production
# Server starts, accepts all requests 🔥
```

### After (Secure)
```bash
# Missing api_key fails fast
ASYNCGATE_ENV=production
# RuntimeError: "api_key is required in production environment"
```

**For production deployment:**
```bash
# Required: Set secure API key
ASYNCGATE_ENV=production
ASYNCGATE_API_KEY=$(openssl rand -hex 32)
```

**For local development:**
```bash
# Option 1: Use insecure dev mode (recommended for local dev)
ASYNCGATE_ENV=development
ASYNCGATE_ALLOW_INSECURE_DEV=true

# Option 2: Use a dev API key
ASYNCGATE_ENV=development
ASYNCGATE_API_KEY=dev-key-12345
```

## Security Benefits

1. **No silent failures**: Server refuses to start with insecure config
2. **Fail closed**: Default behavior denies access, not grants it
3. **Explicit opt-in**: Insecure mode requires deliberate configuration
4. **Environment-aware**: Different rules for dev vs prod
5. **Loud warnings**: Insecure mode logs prominent warnings
6. **Defense in depth**: Both startup and request-time validation

## Implementation Details

**Changed files:**
- `src/asyncgate/api/deps.py`: Hardened `verify_api_key()`, added `validate_auth_config()`
- `src/asyncgate/main.py`: Call validation during startup lifespan
- `src/asyncgate/config.py`: Already had `allow_insecure_dev` defaulting to False

**Error codes:**
- Startup config error → `RuntimeError` (prevents server start)
- Missing api_key at runtime → `503 Service Unavailable` (server misconfigured)
- Invalid api_key → `401 Unauthorized` (bad credentials)
- Missing authorization header → `401 Unauthorized` (no credentials)
