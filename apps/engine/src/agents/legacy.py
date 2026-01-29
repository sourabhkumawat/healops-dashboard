from crewai import Agent, LLM
import os
from src.memory.memory import CodeMemory
from src.config.prompts import CODING_AGENT_PROMPT, RCA_AGENT_PROMPT
from src.core.openrouter_client import get_api_key

# Using OpenRouter for LLM
api_key = get_api_key()
base_url = "https://openrouter.ai/api/v1"

# Cost-effective models via OpenRouter
# Xiaomi MiMo-V2-Flash: Excellent reasoning/coding, 256K context (paid model - free tier ended)
# Grok Code Fast 1: Specialized for agentic coding with reasoning traces - $0.20/M Input, $1.50/M Output
# NOTE: Prefixing with "openai/" forces CrewAI to use the OpenAI protocol (compatible with OpenRouter)
# instead of trying to load native drivers for google/gemini which require GOOGLE_API_KEY.

flash_llm = LLM(
    model="openrouter/xiaomi/mimo-v2-flash",  # LiteLLM format: openrouter/<openrouter-model-id>
    base_url=base_url,
    api_key=api_key
)

coding_llm = LLM(
    model="openai/x-ai/grok-code-fast-1",
    base_url=base_url,
    api_key=api_key
)

# Initialize memory
code_memory = CodeMemory()

def create_agents():
    log_parser = Agent(
        role='Log Parsing Specialist',
        goal='Extract structured signals from raw logs and identify anomalies.',
        backstory='You are an expert in parsing logs from various systems (Kubernetes, Cloud Run, Postgres). You can spot stack traces and error codes instantly.',
        verbose=True,
        allow_delegation=False,
        llm=flash_llm # Use Flash for log processing (high context, low cost)
    )

    rca_analyst = Agent(
        role='Root Cause Analyst',
        goal='Determine the underlying cause of the incident based on parsed logs and system state.',
        backstory=RCA_AGENT_PROMPT,
        verbose=True,
        allow_delegation=True,
        llm=flash_llm # Use Flash for RCA (better reasoning)
    )

    coding_agent = Agent(
        role='Senior Coding Agent',
        goal='Implement code fixes and improvements based on RCA and Safety analysis, utilizing code memory.',
        backstory=CODING_AGENT_PROMPT,
        verbose=True,
        allow_delegation=False,
        llm=coding_llm, 
        memory=True
    )

    safety_officer = Agent(
        role='Safety & Compliance Officer',
        goal='Ensure that proposed healing actions are safe and reversible.',
        backstory='You are responsible for system stability. You reject any action that could cause data loss or downtime without approval.',
        verbose=True,
        allow_delegation=False,
        llm=flash_llm # Use Flash for safety checks (fast, good instruction following)
    )

    return log_parser, rca_analyst, coding_agent, safety_officer
