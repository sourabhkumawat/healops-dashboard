from crewai import Agent
from langchain_community.llms import OpenAI
import os
from memory import CodeMemory
from prompts import CODING_AGENT_PROMPT, RCA_AGENT_PROMPT

# Placeholder for LLM configuration
# In a real scenario, we'd use Azure OpenAI or a local model via Ollama
# For now, we'll assume OpenAI API key is present or we mock it
llm = OpenAI(temperature=0)

# Initialize memory
code_memory = CodeMemory()

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
        backstory=RCA_AGENT_PROMPT,
        verbose=True,
        allow_delegation=True,
        llm=llm
    )

    coding_agent = Agent(
        role='Senior Coding Agent',
        goal='Implement code fixes and improvements based on RCA and Safety analysis, utilizing code memory.',
        backstory=CODING_AGENT_PROMPT,
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
