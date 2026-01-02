"""
Script to repair existing incidents where original_contents might be missing in action_result.
"""
import os
import sys
import json
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Incident, Integration
from ai_analysis import normalize_path
from integrations.github_integration import GithubIntegration

def repair_incidents():
    print("🔧 Starting incident data repair...")
    db = SessionLocal()

    try:
        # Find incidents with action_result
        incidents = db.query(Incident).filter(Incident.action_result.isnot(None)).all()
        print(f"found {len(incidents)} incidents with action_result")

        fixed_count = 0

        for incident in incidents:
            action_result = incident.action_result
            if not isinstance(action_result, dict):
                continue

            changes = action_result.get("changes", {})
            original_contents = action_result.get("original_contents", {})

            if not changes:
                continue

            # Check if repair is needed
            needs_repair = False
            missing_files = []

            for file_path in changes.keys():
                # Check exact match
                if file_path in original_contents:
                    continue

                # Check normalized match
                norm_path = normalize_path(file_path)
                found = False
                for existing_path in original_contents.keys():
                    if normalize_path(existing_path) == norm_path:
                        found = True
                        break

                if not found:
                    needs_repair = True
                    missing_files.append(file_path)

            if needs_repair:
                print(f"🛠️  Repairing Incident #{incident.id}: Missing original content for {missing_files}")

                # Get integration
                if not incident.integration_id:
                    print(f"⚠️  Skipping Incident #{incident.id}: No integration_id")
                    continue

                integration = db.query(Integration).filter(Integration.id == incident.integration_id).first()
                if not integration or integration.provider != "GITHUB":
                    print(f"⚠️  Skipping Incident #{incident.id}: Invalid integration")
                    continue

                # Get repo name
                repo_name = incident.repo_name
                # If repo_name missing, try to derive it (similar logic to main app)
                if not repo_name and integration.config:
                    repo_name = integration.config.get("repo_name")

                if not repo_name:
                    print(f"⚠️  Skipping Incident #{incident.id}: No repo_name")
                    continue

                github = GithubIntegration(integration_id=integration.id)
                repo_info = github.get_repo_info(repo_name)
                default_branch = repo_info.get("default_branch", "main")

                # Fetch missing content
                for file_path in missing_files:
                    try:
                        print(f"   Fetching {file_path} from {repo_name}...")
                        content = github.get_file_contents(repo_name, file_path, ref=default_branch)
                        if content:
                            original_contents[file_path] = content
                            print(f"   ✅ Fetched {len(content)} bytes")
                        else:
                            # Assume new file or deleted
                            original_contents[file_path] = ""
                            print(f"   ⚠️ Content not found, setting as empty")
                    except Exception as e:
                        print(f"   ❌ Error fetching {file_path}: {e}")
                        original_contents[file_path] = ""

                # Update DB
                action_result["original_contents"] = original_contents
                incident.action_result = action_result

                # Force update flag for JSON field
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(incident, "action_result")

                fixed_count += 1

        if fixed_count > 0:
            db.commit()
            print(f"✅ Successfully repaired {fixed_count} incidents")
        else:
            print("✨ No incidents needed repair")

    except Exception as e:
        print(f"❌ Error during repair: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    repair_incidents()
