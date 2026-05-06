# Bionics — UE5 AnimGraph Expert Module
#
# Makes Bionics a UE5 AnimGraph expert via:
#   1. knowledge_base — Complete AnimGraph UI rules, node types, pin semantics
#   2. element_templates — Detection configs for vision-based element matching
#   3. action_sequences — Pre-built, verified workflows (add node, connect, compile)
#   4. animgraph_templates — ActionTemplate subclasses for the template registry
#   5. capture_references — Screenshot capture script for building reference library

from ue5_modules.animgraph.action_sequences import AnimGraphActions
from ue5_modules.animgraph.element_templates import AnimGraphElements
from ue5_modules.animgraph.knowledge_base import AnimGraphKB

__all__ = ["AnimGraphKB", "AnimGraphActions", "AnimGraphElements"]
