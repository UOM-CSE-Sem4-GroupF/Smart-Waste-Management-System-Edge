#!/bin/bash
set -e

BOOTSTRAP=${KAFKA_BOOTSTRAP:-kafka:29092}
RETENTION_MS=604800000  # 7 days in milliseconds

echo "Waiting for Kafka to be ready at $BOOTSTRAP ..."
until kafka-topics --bootstrap-server "$BOOTSTRAP" --list > /dev/null 2>&1; do
  echo "  Kafka not ready yet — retrying in 3s..."
  sleep 3
done
echo "Kafka is ready."

create_topic() {
  local TOPIC=$1
  local PARTITIONS=${2:-3}

  if kafka-topics --bootstrap-server "$BOOTSTRAP" --list | grep -q "^${TOPIC}$"; then
    echo "Topic '$TOPIC' already exists — skipping."
  else
    kafka-topics --bootstrap-server "$BOOTSTRAP" \
      --create \
      --topic "$TOPIC" \
      --partitions "$PARTITIONS" \
      --replication-factor 1 \
      --config retention.ms="$RETENTION_MS"
    echo "Created topic '$TOPIC' (partitions=$PARTITIONS, retention=7d)"
  fi
}

create_topic waste.bin.telemetry 3
create_topic waste.vehicle.location 3
create_topic waste.bridge.dlq 1

echo "All topics ready."
