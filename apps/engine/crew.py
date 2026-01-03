"""
Original crew workflow for incident diagnosis.
Kept for backward compatibility and as a fallback option.

Note: The enhanced crew (enhanced_crew.py) is now used by default.
Set USE_OLD_CREW=true to use this crew explicitly, or it will be used
automatically as a fallback if the enhanced crew fails.
"""
from crewai import Crew, Task
from agents import create_agents
from integrations.github_integration import GithubIntegration

def run_diagnosis_crew(incident_context: dict):
    log_parser, rca_analyst, coding_agent, safety_officer = create_agents()

    # Task 1: Parse the logs
    parse_task = Task(
        description=f"Analyze the following log data and extract key errors: {incident_context.get('message')}",
        agent=log_parser,
        expected_output="A structured summary of the error, including error type, timestamp, and affected service."
    )

    # Task 2: Determine Root Cause
    rca_task = Task(
        description="Based on the parsed logs, identify the root cause. Is it a code bug, infrastructure issue, or external dependency? Provide specific changes needed.",
        agent=rca_analyst,
        expected_output="A definitive root cause analysis with confidence score and a dictionary of file paths and their corrected content."
    )

    # Task 3: Coding Agent Task (New)
    coding_task = Task(
        description="""
        Using the Root Cause Analysis, create the actual code fix.
        1. Consult Code Memory for similar past errors.
        2. Generate the corrected code for the affected files.
        3. If a similar fix exists in memory, reference it.
        4. Output the full content of the corrected files.
        """,
        agent=coding_agent,
        context=[rca_task],
        expected_output="The full path of the files changed and their new content, along with a description of the fix."
    )

    # Task 4: Recommend Action (and check safety)
    safety_task = Task(
        description="Review the proposed root cause and suggested code fix. Validate that the code change is safe. If safe, confirm the action.",
        agent=safety_officer,
        context=[rca_task, coding_task],
        expected_output="A recommended action (e.g., 'Create PR', 'Restart Container') with a safety assessment and verified code changes."
    )

    crew = Crew(
        agents=[log_parser, rca_analyst, coding_agent, safety_officer],
        tasks=[parse_task, rca_task, coding_task, safety_task],
        verbose=2
    )

    result = crew.kickoff()
    
    # After crew finishes, if action is to create PR, do it
    # This is a simplified logic. In a real system, the 'result' would be structured data.
    # For now, we assume the crew returns a string or object that we can parse or use.
    # But since CrewAI returns a string by default, we'd need to parse it or have the agent use a tool.
    
    # Assuming we have integration info in context
    integration_id = incident_context.get("integration_id")
    if integration_id:
        gh = GithubIntegration(integration_id=integration_id)
        # Mocking PR creation for demonstration since we don't have the actual code changes from the text output easily without structured output
        # In a real implementation, we would use a tool inside the agent to create the PR directly.
        pass

    return result
