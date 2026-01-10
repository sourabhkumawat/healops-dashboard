"""
Partition Management for PostgreSQL Partitioned Tables
Handles automatic creation of partitions for the logs table
"""

from datetime import datetime, timedelta
from sqlalchemy import text
from src.database.database import engine
import logging

logger = logging.getLogger(__name__)


def is_logs_table_partitioned() -> bool:
    """
    Check if the logs table is partitioned.
    
    Returns:
        True if the table is partitioned, False otherwise
    """
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                    AND c.relname = 'logs'
                    AND c.relkind = 'p'
                )
            """)
            result = conn.execute(query)
            return result.scalar() or False
    except Exception as e:
        logger.error(f"Error checking if logs table is partitioned: {e}")
        return False


def ensure_partition_exists_for_date(date: datetime) -> bool:
    """
    Ensure a partition exists for the given date.
    Assumes monthly partitioning (one partition per month).
    
    Args:
        date: The datetime for which to ensure a partition exists
        
    Returns:
        True if partition exists or was created, False on error
    """
    try:
        # Check if table is partitioned
        if not is_logs_table_partitioned():
            logger.warning("logs table is not partitioned. Partition creation skipped.")
            return False
        
        # Get the start of the month for the given date
        partition_start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Get the start of the next month
        if partition_start.month == 12:
            partition_end = partition_start.replace(year=partition_start.year + 1, month=1)
        else:
            partition_end = partition_start.replace(month=partition_start.month + 1)
        
        # Format partition name (e.g., logs_2026_01)
        partition_name = f"logs_{partition_start.year}_{partition_start.month:02d}"
        
        # Check if partition already exists
        with engine.begin() as conn:
            check_query = text("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                    AND c.relname = :partition_name
                )
            """)
            result = conn.execute(check_query, {"partition_name": partition_name})
            exists = result.scalar()
            
            if exists:
                logger.debug(f"Partition {partition_name} already exists")
                return True
            
            # Create the partition
            # Note: Using string formatting for table name (safe as it's generated from date)
            create_query = text(f"""
                CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF logs
                FOR VALUES FROM (:start_date) TO (:end_date)
            """)
            
            conn.execute(create_query, {
                "start_date": partition_start,
                "end_date": partition_end
            })
            # Transaction auto-commits on exit from 'with' block
            
            logger.info(f"Created partition {partition_name} for {partition_start.strftime('%Y-%m')}")
            return True
            
    except Exception as e:
        logger.error(f"Error ensuring partition exists for date {date}: {e}")
        return False


def ensure_partition_exists_for_timestamp(timestamp: datetime) -> bool:
    """
    Convenience wrapper that ensures partition exists for a timestamp.
    
    Args:
        timestamp: The timestamp for which to ensure a partition exists
        
    Returns:
        True if partition exists or was created, False on error
    """
    return ensure_partition_exists_for_date(timestamp)


def create_partitions_for_date_range(start_date: datetime, end_date: datetime) -> int:
    """
    Create partitions for a range of dates (monthly partitions).
    
    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        
    Returns:
        Number of partitions created
    """
    created = 0
    current = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    while current <= end:
        if ensure_partition_exists_for_date(current):
            created += 1
        
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    return created


def create_future_partitions(months_ahead: int = 3) -> int:
    """
    Create partitions for future months proactively.
    
    Args:
        months_ahead: Number of months ahead to create partitions for
        
    Returns:
        Number of partitions created
    """
    now = datetime.utcnow()
    end_date = now + timedelta(days=months_ahead * 30)  # Approximate
    return create_partitions_for_date_range(now, end_date)


def get_existing_partitions() -> list:
    """
    Get list of existing partitions for the logs table.
    
    Returns:
        List of partition names
    """
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT 
                    schemaname,
                    tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                AND tablename LIKE 'logs_%'
                ORDER BY tablename
            """)
            result = conn.execute(query)
            return [row[1] for row in result.fetchall()]
    except Exception as e:
        logger.error(f"Error getting existing partitions: {e}")
        return []

