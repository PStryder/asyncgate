-- Migration 002: P1.1 - Add Lease Renewal Tracking
-- 
-- Adds fields to track lease renewals and enforce limits:
-- - acquired_at: When lease was initially acquired (for lifetime tracking)
-- - renewal_count: Number of times lease has been renewed
--
-- These fields enable enforcement of:
-- 1. max_lease_renewals: Prevents infinite renewal loops
-- 2. max_lease_lifetime_seconds: Prevents indefinite lease holding
--
-- Status: IDEMPOTENT (safe to run multiple times)
-- Date: 2026-01-06

BEGIN;

-- Add acquired_at column (when lease was first acquired)
-- Default to created_at for existing leases
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'leases' AND column_name = 'acquired_at'
    ) THEN
        ALTER TABLE leases 
        ADD COLUMN acquired_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();
        
        -- For existing leases, set acquired_at = created_at
        UPDATE leases SET acquired_at = created_at WHERE acquired_at IS NULL;
        
        RAISE NOTICE 'Added acquired_at column to leases table';
    ELSE
        RAISE NOTICE 'acquired_at column already exists, skipping';
    END IF;
END $$;

-- Add renewal_count column (number of times lease has been renewed)
-- Default to 0 for existing leases (assume no renewals yet)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'leases' AND column_name = 'renewal_count'
    ) THEN
        ALTER TABLE leases 
        ADD COLUMN renewal_count INTEGER NOT NULL DEFAULT 0;
        
        RAISE NOTICE 'Added renewal_count column to leases table';
    ELSE
        RAISE NOTICE 'renewal_count column already exists, skipping';
    END IF;
END $$;

-- Add comments for documentation
COMMENT ON COLUMN leases.acquired_at IS 'When lease was initially acquired (P1.1 - for absolute lifetime tracking)';
COMMENT ON COLUMN leases.renewal_count IS 'Number of times lease has been renewed (P1.1 - prevents hoarding DoS)';

COMMIT;

-- Verification queries
\echo ''
\echo '=== Verification ==='
\echo ''

-- Check columns exist
SELECT 
    column_name, 
    data_type, 
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'leases' 
  AND column_name IN ('acquired_at', 'renewal_count')
ORDER BY column_name;

-- Check existing lease data
SELECT 
    COUNT(*) as total_leases,
    COUNT(acquired_at) as with_acquired_at,
    COUNT(renewal_count) as with_renewal_count,
    AVG(renewal_count) as avg_renewals
FROM leases;

\echo ''
\echo '=== Migration 002 Complete ==='
\echo ''
