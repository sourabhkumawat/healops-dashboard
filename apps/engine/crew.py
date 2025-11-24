from crewai import Crew, Task
from agents import create_agents

def run_diagnosis_crew(incident_context: dict):
    log_parser, rca_analyst, safety_officer = create_agents()

    # Task 1: Parse the logs
    parse_task = Task(
        description=f"Analyze the following log data and extract key errors: {incident_context.get('message')}",
        agent=log_parser,
        expected_output="A structured summary of the error, including error type, timestamp, and affected service."
    )

    # Task 2: Determine Root Cause
    rca_task = Task(
        description="Based on the parsed logs, identify the root cause. Is it a code bug, infrastructure issue, or external dependency?",
        agent=rca_analyst,
        expected_output="A definitive root cause analysis with confidence score."
    )

    # Task 3: Recommend Action (and check safety)
    safety_task = Task(
        description="Review the proposed root cause and suggest a safe healing action. If the action is risky, flag it for human approval.",
        agent=safety_officer,
        expected_output="A recommended action (e.g., 'Restart Container', 'Rollback') with a safety assessment."
    )

    crew = Crew(
        agents=[log_parser, rca_analyst, safety_officer],
        tasks=[parse_task, rca_task, safety_task],
        verbose=2
    )

    result = crew.kickoff()
    return result
