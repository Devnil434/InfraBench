-- ==============================================================================
-- InfraBench Database Initialization Script
-- ==============================================================================

-- Create orders table if it doesn't already exist
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 1,
    total_amount DOUBLE PRECISION NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

-- Index on created_at for fast descending sort
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at DESC);

-- Populate initial seed data deterministically if table is empty
INSERT INTO orders (customer_name, item_count, total_amount, status, created_at)
SELECT * FROM (VALUES
    ('Alice Johnson', 3, 149.99, 'COMPLETED', NOW() - INTERVAL '10 minutes'),
    ('Bob Williams', 1, 24.50, 'SHIPPED', NOW() - INTERVAL '9 minutes'),
    ('Charlie Brown', 5, 320.00, 'COMPLETED', NOW() - INTERVAL '8 minutes'),
    ('Diana Prince', 2, 89.90, 'PROCESSING', NOW() - INTERVAL '7 minutes'),
    ('Evan Wright', 4, 210.75, 'PENDING', NOW() - INTERVAL '6 minutes'),
    ('Fiona Gallagher', 1, 15.00, 'CANCELLED', NOW() - INTERVAL '5 minutes'),
    ('George Clark', 8, 540.25, 'COMPLETED', NOW() - INTERVAL '4 minutes'),
    ('Hannah Abbott', 2, 67.80, 'SHIPPED', NOW() - INTERVAL '3 minutes'),
    ('Ian Malcolm', 3, 185.00, 'PROCESSING', NOW() - INTERVAL '2 minutes'),
    ('Julia Roberts', 6, 420.50, 'COMPLETED', NOW() - INTERVAL '1 minute')
) AS seed(customer_name, item_count, total_amount, status, created_at)
WHERE NOT EXISTS (SELECT 1 FROM orders);
