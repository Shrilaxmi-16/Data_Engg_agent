#!/bin/bash
set -e
TPCDS_DIR=~/Documents/Shrilaxmi/Data_Engg/datasets/tpc-ds/tpcds-kit/tools/output
CONTAINER=shield-postgres

# Order: independent dims first, then dependent dims, then facts
TABLES=(
    call_center catalog_page customer_address customer_demographics
    date_dim household_demographics income_band item promotion reason
    ship_mode store time_dim warehouse web_page web_site
    customer
    web_sales catalog_sales store_sales
    web_returns catalog_returns store_returns
    inventory
)

echo "Loading TPC-DS tables..."
for tbl in "${TABLES[@]}"; do
    echo "  -> ${tbl}"
    sed 's/|$//' "${TPCDS_DIR}/${tbl}.dat" | docker exec -i ${CONTAINER} psql -U shield -d shield_db -c "\COPY tpcds.${tbl} FROM STDIN WITH (FORMAT csv, DELIMITER '|')"
done
echo "TPC-DS load complete."
