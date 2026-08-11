#!/bin/bash
set -e
TPCH_DIR=~/Documents/Shrilaxmi/Data_Engg/datasets/tpc-h/tpch-kit/dbgen
CONTAINER=shield-postgres

echo "Loading TPC-H tables..."
for tbl in region nation supplier customer part partsupp orders lineitem; do
    echo "  -> ${tbl}"
    sed 's/|$//' "${TPCH_DIR}/${tbl}.tbl" | docker exec -i ${CONTAINER} psql -U shield -d shield_db -c "\COPY tpch.${tbl} FROM STDIN WITH (FORMAT csv, DELIMITER '|')"
done
echo "TPC-H load complete."
