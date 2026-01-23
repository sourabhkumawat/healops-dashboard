#!/usr/bin/env python3
"""
Setup script to create necessary Redpanda topics for Healops.
Run this after starting Redpanda to ensure topics exist.
"""
import os
import sys
import time
from kafka import KafkaProducer, KafkaConsumer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError, NoBrokersAvailable

# Redpanda Configuration
REDPANDA_BROKERS = os.getenv("REDPANDA_BROKERS", "localhost:19092")
REDPANDA_LOG_TOPIC = os.getenv("REDPANDA_LOG_TOPIC", "healops-logs")
REDPANDA_INCIDENT_TOPIC = os.getenv("REDPANDA_INCIDENT_TOPIC", "healops-incidents")

def wait_for_redpanda(max_retries=30, retry_interval=2):
    """Wait for Redpanda to be available."""
    print(f"Waiting for Redpanda at {REDPANDA_BROKERS}...")

    for attempt in range(max_retries):
        try:
            producer = KafkaProducer(bootstrap_servers=[REDPANDA_BROKERS], request_timeout_ms=5000)
            producer.close()
            print("✓ Redpanda is available")
            return True
        except NoBrokersAvailable:
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries} - waiting {retry_interval}s...")
                time.sleep(retry_interval)
            else:
                print(f"✗ Redpanda not available after {max_retries} attempts")
                return False
        except Exception as e:
            print(f"  Error connecting to Redpanda: {e}")
            return False

    return False

def create_topics():
    """Create necessary topics in Redpanda."""
    try:
        admin_client = KafkaAdminClient(
            bootstrap_servers=[REDPANDA_BROKERS],
            client_id='healops-topic-creator'
        )

        topics = [
            NewTopic(
                name=REDPANDA_LOG_TOPIC,
                num_partitions=3,  # Multiple partitions for better throughput
                replication_factor=1,  # Single node setup
                topic_configs={
                    'cleanup.policy': 'delete',
                    'retention.ms': '604800000',  # 7 days retention
                    'compression.type': 'snappy'
                }
            ),
            NewTopic(
                name=REDPANDA_INCIDENT_TOPIC,
                num_partitions=2,  # Fewer partitions for task ordering
                replication_factor=1,
                topic_configs={
                    'cleanup.policy': 'delete',
                    'retention.ms': '1209600000',  # 14 days retention
                    'compression.type': 'snappy'
                }
            )
        ]

        print("Creating topics...")
        fs = admin_client.create_topics(new_topics=topics, validate_only=False)

        for topic_name, future in fs.items():
            try:
                future.result()  # The result is None if successful
                print(f"✓ Created topic: {topic_name}")
            except TopicAlreadyExistsError:
                print(f"✓ Topic already exists: {topic_name}")
            except Exception as e:
                print(f"✗ Failed to create topic {topic_name}: {e}")
                return False

        admin_client.close()
        return True

    except Exception as e:
        print(f"✗ Error creating topics: {e}")
        return False

def verify_topics():
    """Verify that topics were created successfully."""
    try:
        admin_client = KafkaAdminClient(
            bootstrap_servers=[REDPANDA_BROKERS],
            client_id='healops-topic-verifier'
        )

        metadata = admin_client.list_topics(timeout_ms=10000)
        topics = metadata.topics

        print("\nVerifying topics:")
        for topic_name in [REDPANDA_LOG_TOPIC, REDPANDA_INCIDENT_TOPIC]:
            if topic_name in topics:
                partitions = len(topics[topic_name].partitions)
                print(f"✓ {topic_name} ({partitions} partitions)")
            else:
                print(f"✗ {topic_name} not found")
                return False

        admin_client.close()
        return True

    except Exception as e:
        print(f"✗ Error verifying topics: {e}")
        return False

def test_produce_consume():
    """Test basic produce/consume functionality."""
    try:
        print("\nTesting produce/consume...")

        # Test producer
        producer = KafkaProducer(
            bootstrap_servers=[REDPANDA_BROKERS],
            value_serializer=lambda v: str(v).encode('utf-8')
        )

        producer.send(REDPANDA_LOG_TOPIC, 'test-message')
        producer.flush()
        producer.close()
        print("✓ Producer test successful")

        # Test consumer
        consumer = KafkaConsumer(
            REDPANDA_LOG_TOPIC,
            bootstrap_servers=[REDPANDA_BROKERS],
            auto_offset_reset='earliest',
            consumer_timeout_ms=5000
        )

        message_count = 0
        for message in consumer:
            message_count += 1
            if message_count >= 1:  # Just need to read one message
                break

        consumer.close()
        print("✓ Consumer test successful")
        return True

    except Exception as e:
        print(f"✗ Produce/consume test failed: {e}")
        return False

def main():
    """Main setup function."""
    print("Healops Redpanda Setup")
    print("======================")
    print(f"Broker: {REDPANDA_BROKERS}")
    print(f"Topics: {REDPANDA_LOG_TOPIC}, {REDPANDA_INCIDENT_TOPIC}")
    print()

    # Step 1: Wait for Redpanda
    if not wait_for_redpanda():
        print("\n✗ Setup failed: Redpanda not available")
        print("Make sure Redpanda is running:")
        print("  docker-compose -f docker-compose.redpanda.yml up -d")
        sys.exit(1)

    # Step 2: Create topics
    if not create_topics():
        print("\n✗ Setup failed: Could not create topics")
        sys.exit(1)

    # Step 3: Verify topics
    if not verify_topics():
        print("\n✗ Setup failed: Topic verification failed")
        sys.exit(1)

    # Step 4: Test functionality
    if not test_produce_consume():
        print("\n✗ Setup failed: Produce/consume test failed")
        sys.exit(1)

    print("\n🎉 Redpanda setup completed successfully!")
    print("\nNext steps:")
    print("1. Start your Healops engine: python main.py")
    print("2. Send logs to your API endpoints")
    print("3. Monitor topics via Redpanda Console: http://localhost:8081")

if __name__ == "__main__":
    main()