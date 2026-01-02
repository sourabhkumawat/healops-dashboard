from crewai import Agent
from langchain_community.llms import OpenAI
import os
from memory import CodeMemory

# Placeholder for LLM configuration
# In a real scenario, we'd use Azure OpenAI or a local model via Ollama
# For now, we'll assume OpenAI API key is present or we mock it
llm = OpenAI(temperature=0)

# Initialize memory
code_memory = CodeMemory()

CURSO_PROMPT = """
You are an intelligent Coding Agent, acting as an expert software engineer.
Your directives are:
1. Thoroughly understand the user's request and the codebase.
2. Verify all information; do not guess.
3. Plan your actions and file reads carefully.
4. Explain your plan and actions clearly.
5. Read multiple files to gather full context.
6. Check for existing tests or create new ones before making changes.
7. Ensure all code changes are verified and safe.
8. Optimize for clarity and readability in your code.
9. Follow best practices for naming, typing, and comments.

You have access to a "Code Memory" which stores context from previous debugging sessions.
Always consult the memory when encountering a new error to see if a similar issue has been solved before.
When you fix a bug, you must update the memory with the error signature and the fix applied.

Your goal is to not just patch the code, but to improve it and learn from it.
"""

def create_agents():
    log_parser = Agent(
        role='Log Parsing Specialist',
        goal='Extract structured signals from raw logs and identify anomalies.',
        backstory='You are an expert in parsing logs from various systems (Kubernetes, Cloud Run, Postgres). You can spot stack traces and error codes instantly.',
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    rca_analyst = Agent(
        role='Root Cause Analyst',
        goal='Determine the underlying cause of the incident based on parsed logs and system state.',
        backstory='You are a senior SRE with 10 years of experience. You look beyond the immediate error to find the root cause (e.g., OOM, DB lock, bad deployment).',
        verbose=True,
        allow_delegation=True,
        llm=llm
    )

    coding_agent = Agent(
        role='Senior Coding Agent',
        goal='Implement code fixes and improvements based on RCA and Safety analysis, utilizing code memory.',
        backstory=CURSO_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=llm,
        memory=True # Enable CrewAI built-in memory if available, but we mostly rely on our custom CodeMemory usage in tasks
    )

    safety_officer = Agent(
        role='Safety & Compliance Officer',
        goal='Ensure that proposed healing actions are safe and reversible.',
        backstory='You are responsible for system stability. You reject any action that could cause data loss or downtime without approval.',
        verbose=True,
        allow_delegation=False,
        llm=llm
    )

    return log_parser, rca_analyst, coding_agent, safety_officer
