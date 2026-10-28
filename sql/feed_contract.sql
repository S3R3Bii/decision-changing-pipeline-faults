-- Independent feed-grain and reconciliation audit. Header values are bound parameters.
SELECT COUNT(*) AS row_count,
       COUNT(DISTINCT product) AS distinct_products,
       SUM(count_value) AS payload_total,
       SUM(CASE WHEN typeof(count_value) != 'integer' OR count_value < 0 THEN 1 ELSE 0 END) AS invalid_counts,
       SUM(CASE WHEN period != ? THEN 1 ELSE 0 END) AS wrong_periods,
       SUM(CASE WHEN product NOT IN (SELECT product FROM expected_products) THEN 1 ELSE 0 END) AS unknown_products
FROM feed;
