"""
Context Manager for intelligent context window management with prioritization.
Manages context parts with priorities and builds optimized context strings.
"""
from typing import List, Dict, Any, Optional
import os

class ContextManager:
    """
    Manus-style context management with prioritization.
    
    Manages context parts with priorities and builds optimized context strings
    that fit within token limits while preserving most important information.
    """
    
    def __init__(self, max_context_tokens: int = None):
        """
        Initialize context manager.
        
        Args:
            max_context_tokens: Maximum context tokens (default from env or 80000)
        """
        self.max_context_tokens = max_context_tokens or int(os.getenv("CONTEXT_MAX_TOKENS", "80000"))
        self.context_parts: List[Dict[str, Any]] = []
    
    def add_context(
        self, 
        content: str, 
        priority: int = 5, 
        category: str = "general"
    ):
        """
        Add context with priority.
        
        Args:
            content: Context content string
            priority: Priority level (1=low, 5=medium, 10=high)
            category: Category for grouping (root_cause, files, memory, knowledge, events, plan)
        """
        tokens = self._estimate_tokens(content)
        self.context_parts.append({
            "content": content,
            "priority": priority,
            "category": category,
            "tokens": tokens
        })
    
    def add_knowledge(self, knowledge_items: List[Dict[str, Any]]):
        """
        Add knowledge items with high priority based on relevance.
        
        Args:
            knowledge_items: List of knowledge items with relevance_score
        """
        for item in knowledge_items:
            # High priority for highly relevant knowledge
            relevance = item.get("relevance_score", 0.5)
            priority = 10 if relevance > 0.8 else 8 if relevance > 0.6 else 5
            
            knowledge_text = f"[Knowledge - Relevance: {relevance:.2f}]\n{item.get('content', '')[:500]}"
            if item.get("metadata"):
                knowledge_text += f"\nSource: {item['metadata'].get('type', 'unknown')}"
            
            self.add_context(
                content=knowledge_text,
                priority=priority,
                category="knowledge"
            )
    
    def build_context(
        self, 
        event_stream_context: str = "",
        current_step: Optional[Dict[str, Any]] = None,
        workspace_state: Optional[str] = None
    ) -> str:
        """
        Build optimized context string.
        
        Args:
            event_stream_context: Event stream context string
            current_step: Current step dictionary
            workspace_state: Workspace state string
            
        Returns:
            Optimized context string
        """
        # Sort by priority (highest first)
        sorted_parts = sorted(self.context_parts, key=lambda x: x["priority"], reverse=True)
        
        # Start with event stream and current step (always included)
        context_lines = [
            "# Current Context",
            ""
        ]
        
        total_tokens = self._estimate_tokens("\n".join(context_lines))
        
        # Add current step (highest priority)
        if current_step:
            step_context = f"## Current Step: {current_step.get('step_number', 'N/A')} - {current_step.get('description', 'N/A')}"
            if current_step.get('expected_output'):
                step_context += f"\nExpected Output: {current_step['expected_output']}"
            step_tokens = self._estimate_tokens(step_context)
            context_lines.append(step_context)
            context_lines.append("")
            total_tokens += step_tokens
        
        # Add workspace state if provided
        if workspace_state:
            ws_tokens = self._estimate_tokens(workspace_state)
            if total_tokens + ws_tokens <= self.max_context_tokens * 0.9:  # Reserve 10% for other content
                context_lines.append("## Workspace State:")
                context_lines.append(workspace_state)
                context_lines.append("")
                total_tokens += ws_tokens
        
        # Add event stream context
        if event_stream_context:
            event_tokens = self._estimate_tokens(event_stream_context)
            if total_tokens + event_tokens <= self.max_context_tokens * 0.8:  # Reserve 20% for other content
                context_lines.append("## Recent Events:")
                context_lines.append(event_stream_context)
                context_lines.append("")
                total_tokens += event_tokens
        
        # Add high-priority context parts
        context_lines.append("## Relevant Context:")
        context_lines.append("")
        
        for part in sorted_parts:
            part_tokens = part["tokens"]
            
            # Check if adding this part would exceed limit
            if total_tokens + part_tokens > self.max_context_tokens * 0.95:  # Stop at 95% to be safe
                # Summarize remaining parts
                remaining = [p for p in sorted_parts if sorted_parts.index(p) >= sorted_parts.index(part)]
                if remaining:
                    summary = self._summarize_context_parts(remaining)
                    context_lines.append(f"\n## Summary of Additional Context:\n{summary}")
                break
            
            # Add category header if first of this category
            category = part["category"]
            if not any(f"### {category}" in line for line in context_lines):
                context_lines.append(f"### {category.replace('_', ' ').title()}:")
            
            context_lines.append(part["content"])
            context_lines.append("")
            total_tokens += part_tokens
        
        return "\n".join(context_lines)
    
    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.
        
        Args:
            text: Text to estimate
            
        Returns:
            Estimated token count
        """
        if not text:
            return 0
        # Conservative estimate: 3.5 characters per token
        return int(len(text) / 3.5)
    
    def _summarize_context_parts(self, parts: List[Dict[str, Any]]) -> str:
        """
        Summarize context parts that don't fit.
        
        Args:
            parts: List of context parts to summarize
            
        Returns:
            Summary string
        """
        categories = {}
        for part in parts:
            cat = part["category"]
            if cat not in categories:
                categories[cat] = []
            # Get preview of content
            content_preview = part["content"][:200] if len(part["content"]) > 200 else part["content"]
            categories[cat].append(content_preview)
        
        summary_lines = []
        for cat, contents in categories.items():
            summary_lines.append(f"{cat.replace('_', ' ').title()}: {len(contents)} items")
            # Show first item as example
            if contents:
                summary_lines.append(f"  Example: {contents[0][:100]}...")
        
        return "\n".join(summary_lines)
    
    def clear(self):
        """Clear all context parts."""
        self.context_parts = []
    
    def get_total_tokens(self) -> int:
        """Get total estimated tokens for all context parts."""
        return sum(part["tokens"] for part in self.context_parts)

