"""
Confidence scoring algorithms for autonomous decision making.
"""
from typing import Dict, Any, List, Optional
import re
from src.memory.memory import CodeMemory

class ConfidenceScorer:
    """Scores fixes for confidence, risk, and quality."""
    
    def __init__(self):
        self.code_memory = CodeMemory()
    
    def calculate_confidence(
        self,
        fix_data: Dict[str, Any],
        validation_results: Dict[str, Any],
        impact_analysis: Dict[str, Any],
        pattern_consistency: Dict[str, Any],
        error_signature: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculate overall confidence score for a fix.
        
        Args:
            fix_data: Fix information (files changed, edits, etc.)
            validation_results: Syntax validation results
            impact_analysis: Impact analysis results
            pattern_consistency: Pattern consistency results
            error_signature: Optional error signature for memory comparison
            
        Returns:
            Confidence score breakdown and overall score
        """
        scores = {}
        
        # 1. Code Quality Score (30%)
        scores["code_quality"] = self._score_code_quality(
            fix_data,
            validation_results
        )
        
        # 2. Fix Accuracy Score (40%)
        scores["fix_accuracy"] = self._score_fix_accuracy(
            fix_data,
            validation_results
        )
        
        # 3. Risk Assessment (20%)
        scores["risk"] = self._assess_risk(
            impact_analysis,
            fix_data
        )
        risk_score = 100 - (scores["risk"]["level_score"] * 20)  # Convert risk to score
        
        # 4. Memory Match Score (10%)
        memory_score = 50  # Default
        if error_signature:
            memory_score = self._score_memory_match(error_signature, fix_data)
        
        # Calculate weighted overall score
        overall = (
            scores["code_quality"] * 0.30 +
            scores["fix_accuracy"] * 0.40 +
            risk_score * 0.20 +
            memory_score * 0.10
        )
        
        return {
            "overall_confidence": round(overall, 2),
            "code_quality": scores["code_quality"],
            "fix_accuracy": scores["fix_accuracy"],
            "risk_score": risk_score,
            "risk_level": scores["risk"]["level"],
            "memory_match": memory_score,
            "breakdown": scores
        }
    
    def _score_code_quality(
        self,
        fix_data: Dict[str, Any],
        validation_results: Dict[str, Any]
    ) -> float:
        """Score code quality (0-100)."""
        score = 100.0
        
        # Syntax errors reduce score significantly
        if validation_results.get("syntax_errors"):
            syntax_errors = len(validation_results["syntax_errors"])
            score -= min(syntax_errors * 20, 80)  # Max -80 for syntax errors
        
        # Check if validation passed
        if not validation_results.get("all_valid", False):
            score -= 30
        
        # Check for basic structure issues
        if validation_results.get("structure_issues"):
            score -= len(validation_results["structure_issues"]) * 5
        
        return max(score, 0.0)
    
    def _score_fix_accuracy(
        self,
        fix_data: Dict[str, Any],
        validation_results: Dict[str, Any]
    ) -> float:
        """Score fix accuracy (0-100)."""
        score = 100.0
        
        # Minimal changes are better
        total_changes = fix_data.get("total_changes", 0)
        if total_changes > 50:
            score -= 20  # Too many changes
        elif total_changes > 20:
            score -= 10
        
        # Check if fix addresses the issue
        if not fix_data.get("addresses_root_cause", True):
            score -= 40
        
        # Incremental edits are better than full regeneration
        if fix_data.get("full_regeneration", False):
            score -= 15
        
        return max(score, 0.0)
    
    def _assess_risk(
        self,
        impact_analysis: Dict[str, Any],
        fix_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Assess risk level."""
        risk_factors = []
        risk_score = 0
        
        # Breaking changes
        if impact_analysis.get("breaking_changes"):
            breaking = impact_analysis["breaking_changes"]
            risk_factors.append(f"Breaking changes: {len(breaking)}")
            risk_score += len(breaking) * 2
        
        # Dependent files
        dependents = impact_analysis.get("dependent_files", [])
        if len(dependents) > 10:
            risk_factors.append(f"Many dependent files: {len(dependents)}")
            risk_score += 2
        elif len(dependents) > 5:
            risk_score += 1
        
        # API changes
        if impact_analysis.get("api_changes"):
            risk_factors.append("API changes detected")
            risk_score += 3
        
        # Determine risk level
        if risk_score >= 5:
            level = "High"
            level_score = 3
        elif risk_score >= 2:
            level = "Medium"
            level_score = 2
        else:
            level = "Low"
            level_score = 1
        
        return {
            "level": level,
            "level_score": level_score,
            "risk_score": risk_score,
            "factors": risk_factors
        }
    
    def _score_memory_match(
        self,
        error_signature: str,
        fix_data: Dict[str, Any]
    ) -> float:
        """Score similarity to past successful fixes (0-100)."""
        try:
            memory_data = self.code_memory.retrieve_context(error_signature)
            known_fixes = memory_data.get("known_fixes", [])
            
            if not known_fixes:
                return 50  # No memory, neutral score
            
            # Simple similarity check
            # In a real implementation, this would be more sophisticated
            fix_files = set(fix_data.get("files_changed", []))
            
            best_match = 0
            for past_fix in known_fixes:
                # Extract file paths from past fix (would need parsing)
                # For now, just check if we have any past fixes
                best_match = max(best_match, 70)  # Having past fixes increases confidence
            
            return min(best_match, 100)
        except Exception as e:
            print(f"Error scoring memory match: {e}")
            return 50
    
    def make_decision(
        self,
        confidence_score: float,
        risk_level: str,
        validation_passed: bool
    ) -> Dict[str, Any]:
        """
        Make autonomous decision based on confidence and risk.
        
        Returns:
            Decision with action and reasoning
        """
        # Decision thresholds
        HIGH_CONFIDENCE = 90.0
        MEDIUM_CONFIDENCE = 70.0
        
        decision = {
            "confidence": confidence_score,
            "risk_level": risk_level,
            "validation_passed": validation_passed
        }
        
        # Decision logic
        if not validation_passed:
            decision["action"] = "SKIP_PR"
            decision["reasoning"] = "Validation failed - cannot create PR"
            decision["warnings"] = ["Syntax validation failed"]
        
        elif confidence_score >= HIGH_CONFIDENCE and risk_level == "Low":
            decision["action"] = "CREATE_PR"
            decision["reasoning"] = f"High confidence ({confidence_score}%) with low risk"
            decision["warnings"] = []
        
        elif confidence_score >= MEDIUM_CONFIDENCE:
            decision["action"] = "CREATE_PR_WITH_WARNINGS"
            decision["reasoning"] = f"Medium confidence ({confidence_score}%) - review recommended"
            warnings = []
            if risk_level != "Low":
                warnings.append(f"Risk level: {risk_level}")
            if confidence_score < HIGH_CONFIDENCE:
                warnings.append("Confidence below high threshold")
            decision["warnings"] = warnings
        
        else:
            decision["action"] = "CREATE_DRAFT_PR"
            decision["reasoning"] = f"Low confidence ({confidence_score}%) - created draft PR for user review"
            decision["warnings"] = [
                f"⚠️ LOW CONFIDENCE: {confidence_score}% (below 70% threshold)",
                f"⚠️ RISK LEVEL: {risk_level}",
                "⚠️ Draft PR created - please review changes before merging",
                "⚠️ File changes are available on the incident page for cross-checking"
            ]
        
        return decision

def compare_fixes(
    fixes: List[Dict[str, Any]],
    scorer: ConfidenceScorer
) -> List[Dict[str, Any]]:
    """
    Compare multiple fixes and rank them.
    
    Args:
        fixes: List of fix data with validation/impact results
        scorer: ConfidenceScorer instance
        
    Returns:
        Ranked list of fixes with scores
    """
    scored_fixes = []
    
    for i, fix in enumerate(fixes):
        confidence = scorer.calculate_confidence(
            fix_data=fix.get("fix_data", {}),
            validation_results=fix.get("validation", {}),
            impact_analysis=fix.get("impact", {}),
            pattern_consistency=fix.get("pattern", {}),
            error_signature=fix.get("error_signature")
        )
        
        scored_fixes.append({
            "fix_index": i,
            "confidence": confidence,
            "fix_data": fix.get("fix_data", {})
        })
    
    # Sort by confidence (highest first)
    scored_fixes.sort(key=lambda x: x["confidence"]["overall_confidence"], reverse=True)
    
    # Add ranking
    for rank, fix in enumerate(scored_fixes, 1):
        fix["rank"] = rank
    
    return scored_fixes

