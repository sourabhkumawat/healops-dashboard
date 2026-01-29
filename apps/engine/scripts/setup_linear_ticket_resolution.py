#!/usr/bin/env python3
"""
Linear Ticket Resolution Setup Script

This script helps set up the Linear ticket resolution feature by:
1. Running database migrations
2. Configuring environment variables
3. Setting up the polling service
4. Validating the configuration
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from typing import Dict, Any

# Add the engine directory to the Python path
engine_root = Path(__file__).parent.parent
sys.path.insert(0, str(engine_root))

from sqlalchemy.orm import Session
from src.database.database import SessionLocal
from src.database.models import Integration, User
from src.services.linear_polling_service import PollingConfig, LinearPollingService
from src.integrations.linear.integration import LinearIntegration
from src.core.openrouter_client import get_api_key


class LinearTicketResolutionSetup:
    """Setup manager for Linear ticket resolution feature."""

    def __init__(self):
        self.db = SessionLocal()
        self.config_file = engine_root / "config" / "linear_ticket_resolution.json"

    def run_setup(self):
        """Run the complete setup process."""
        print("🚀 Setting up Linear Ticket Resolution Feature")
        print("=" * 50)

        try:
            # Step 1: Check prerequisites
            print("\n1️⃣  Checking prerequisites...")
            self._check_prerequisites()

            # Step 2: Run database migrations
            print("\n2️⃣  Running database migrations...")
            self._run_migrations()

            # Step 3: Configure environment
            print("\n3️⃣  Configuring environment...")
            self._configure_environment()

            # Step 4: Test Linear integrations
            print("\n4️⃣  Testing Linear integrations...")
            self._test_integrations()

            # Step 5: Configure polling service
            print("\n5️⃣  Configuring polling service...")
            self._configure_polling_service()

            # Step 6: Validate setup
            print("\n6️⃣  Validating setup...")
            self._validate_setup()

            print("\n✅ Linear Ticket Resolution setup completed successfully!")
            print("\nNext steps:")
            print("1. Configure your Linear integrations with auto-resolution settings")
            print("2. Start the polling service: python -m src.services.linear_polling_service")
            print("3. Monitor resolution attempts via the API endpoints")

        except Exception as e:
            print(f"\n❌ Setup failed: {e}")
            sys.exit(1)
        finally:
            self.db.close()

    def _check_prerequisites(self):
        """Check that all prerequisites are met."""
        print("  • Checking database connection...")
        try:
            self.db.execute("SELECT 1")
            print("    ✅ Database connection OK")
        except Exception as e:
            raise Exception(f"Database connection failed: {e}")

        print("  • Checking required environment variables...")
        required_vars = [
            ("DATABASE_URL", lambda: os.getenv("DATABASE_URL")),
            ("OPENCOUNCIL_API", get_api_key),  # OpenRouter / AI analysis
        ]

        missing_vars = []
        for name, getter in required_vars:
            if not getter():
                missing_vars.append(name)
            else:
                print(f"    ✅ {name} configured")

        if missing_vars:
            raise Exception(f"Missing environment variables: {', '.join(missing_vars)}")

        print("  • Checking for existing Linear integrations...")
        linear_integrations = self.db.query(Integration).filter(
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        ).count()

        if linear_integrations == 0:
            print("    ⚠️  No active Linear integrations found")
            print("    📝 You'll need to set up Linear integrations before using this feature")
        else:
            print(f"    ✅ Found {linear_integrations} active Linear integrations")

    def _run_migrations(self):
        """Run database migrations."""
        print("  • Running migration for linear_resolution_attempts table...")

        try:
            # Import and run the migration
            from migrations.add_linear_resolution_attempts import run_migration
            run_migration()
            print("    ✅ Database migrations completed")
        except Exception as e:
            print(f"    ❌ Migration failed: {e}")
            print("    📝 You may need to run the migration manually:")
            print("    python migrations/add_linear_resolution_attempts.py")
            raise

    def _configure_environment(self):
        """Configure environment variables and create config file."""
        print("  • Creating configuration file...")

        # Default configuration
        config = {
            "polling_service": {
                "enabled": True,
                "polling_interval_minutes": 15,
                "max_tickets_per_integration": 5,
                "max_concurrent_integrations": 3,
                "max_consecutive_errors": 5,
                "error_backoff_minutes": 30,
                "enable_email_alerts": False,
                "admin_email": None
            },
            "resolution_defaults": {
                "confidence_threshold": 0.5,
                "max_priority": 2,
                "excluded_labels": ["manual-only", "design", "blocked"],
                "require_approval": False
            }
        }

        # Create config directory if it doesn't exist
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

        # Write config file
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"    ✅ Configuration file created: {self.config_file}")

        # Check for optional environment variables
        optional_vars = {
            "LINEAR_POLLING_INTERVAL_MINUTES": "15",
            "LINEAR_MAX_TICKETS_PER_INTEGRATION": "5",
            "LINEAR_MAX_CONCURRENT_INTEGRATIONS": "3",
            "LINEAR_ENABLE_EMAIL_ALERTS": "false",
            "LINEAR_ADMIN_EMAIL": None
        }

        print("  • Optional environment variables:")
        for var, default in optional_vars.items():
            value = os.getenv(var, default)
            if value:
                print(f"    📝 {var}: {value}")

    def _test_integrations(self):
        """Test existing Linear integrations."""
        integrations = self.db.query(Integration).filter(
            Integration.provider == "LINEAR",
            Integration.status == "ACTIVE"
        ).all()

        if not integrations:
            print("    ⚠️  No Linear integrations to test")
            return

        for integration in integrations[:3]:  # Test first 3 integrations
            print(f"  • Testing integration {integration.id} ({integration.name})...")

            try:
                linear = LinearIntegration(integration_id=integration.id)

                # Test connection
                connection_result = linear.verify_connection()
                if connection_result.get("status") == "verified":
                    print(f"    ✅ Connection verified for {integration.name}")

                    # Test fetching teams
                    teams = linear.get_teams()
                    print(f"    📋 Found {len(teams)} teams")

                    # Test fetching open issues (limit to 1)
                    issues = linear.get_open_resolvable_issues()[:1]
                    if issues:
                        print(f"    🎯 Found {len(issues)} resolvable issues")
                    else:
                        print("    📝 No resolvable issues found")

                else:
                    print(f"    ❌ Connection failed for {integration.name}: {connection_result.get('message')}")

            except Exception as e:
                print(f"    ❌ Error testing {integration.name}: {e}")

    def _configure_polling_service(self):
        """Configure the polling service."""
        print("  • Creating polling service configuration...")

        # Load config from file
        with open(self.config_file, 'r') as f:
            config_data = json.load(f)

        polling_config = config_data["polling_service"]

        # Create PollingConfig object
        config = PollingConfig(
            polling_interval_minutes=polling_config["polling_interval_minutes"],
            max_tickets_per_integration=polling_config["max_tickets_per_integration"],
            max_concurrent_integrations=polling_config["max_concurrent_integrations"],
            max_consecutive_errors=polling_config["max_consecutive_errors"],
            error_backoff_minutes=polling_config["error_backoff_minutes"],
            enable_email_alerts=polling_config["enable_email_alerts"],
            admin_email=polling_config["admin_email"]
        )

        print(f"    ✅ Polling interval: {config.polling_interval_minutes} minutes")
        print(f"    ✅ Max tickets per integration: {config.max_tickets_per_integration}")
        print(f"    ✅ Max concurrent integrations: {config.max_concurrent_integrations}")

        # Test service initialization (don't start it)
        try:
            service = LinearPollingService(config)
            print("    ✅ Polling service configuration validated")
        except Exception as e:
            print(f"    ❌ Polling service configuration error: {e}")
            raise

    def _validate_setup(self):
        """Validate the complete setup."""
        print("  • Validating database tables...")

        try:
            from src.database.models import LinearResolutionAttempt
            # Try to query the table
            self.db.query(LinearResolutionAttempt).limit(1).all()
            print("    ✅ LinearResolutionAttempt table accessible")
        except Exception as e:
            print(f"    ❌ Database validation failed: {e}")
            raise

        print("  • Validating API endpoints...")
        try:
            # Import the controller to check for import errors
            from src.api.controllers.linear_ticket_controller import router
            print("    ✅ API controller imports successfully")
        except Exception as e:
            print(f"    ❌ API controller validation failed: {e}")
            raise

        print("  • Validating Linear analyzer...")
        try:
            from src.core.linear_ticket_analyzer import LinearTicketAnalyzer
            print("    ✅ Linear ticket analyzer imports successfully")
        except Exception as e:
            print(f"    ❌ Analyzer validation failed: {e}")
            raise

        print("  • Validating ticket resolver...")
        try:
            from src.services.linear_ticket_resolver import LinearTicketResolver
            print("    ✅ Ticket resolver imports successfully")
        except Exception as e:
            print(f"    ❌ Resolver validation failed: {e}")
            raise

    def print_usage_guide(self):
        """Print a usage guide for the feature."""
        print("\n📚 Linear Ticket Resolution Usage Guide")
        print("=" * 50)

        print("\n🔧 Configuration API Endpoints:")
        print("GET    /linear-tickets/integrations/{id}/config")
        print("PUT    /linear-tickets/integrations/{id}/config")
        print("GET    /linear-tickets/integrations/{id}/teams")

        print("\n🎯 Ticket Management:")
        print("GET    /linear-tickets/integrations/{id}/resolvable-tickets")
        print("POST   /linear-tickets/integrations/{id}/analyze-ticket")
        print("POST   /linear-tickets/integrations/{id}/resolve-ticket")

        print("\n📊 Monitoring:")
        print("GET    /linear-tickets/integrations/{id}/resolution-attempts")
        print("GET    /linear-tickets/integrations/{id}/analytics")

        print("\n🤖 Polling Service Control:")
        print("POST   /linear-tickets/polling-service/start")
        print("POST   /linear-tickets/polling-service/stop")
        print("GET    /linear-tickets/polling-service/status")

        print("\n📁 Configuration Files:")
        print(f"Config: {self.config_file}")

        print("\n🚀 Starting the Polling Service:")
        print("python -m src.services.linear_polling_service")

        print("\n📝 Environment Variables:")
        print("LINEAR_POLLING_INTERVAL_MINUTES=15")
        print("LINEAR_MAX_TICKETS_PER_INTEGRATION=5")
        print("LINEAR_ENABLE_EMAIL_ALERTS=false")
        print("LINEAR_ADMIN_EMAIL=admin@yourcompany.com")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Linear Ticket Resolution Setup")
    parser.add_argument("--guide", action="store_true", help="Show usage guide only")

    args = parser.parse_args()

    setup = LinearTicketResolutionSetup()

    if args.guide:
        setup.print_usage_guide()
    else:
        setup.run_setup()
        print("\n" + "=" * 50)
        setup.print_usage_guide()


if __name__ == "__main__":
    main()