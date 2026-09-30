"""Jurisdiction scoping for graph reads (FR-10).

A node is "in scope" for a jurisdiction-limited officer when it is anchored to a FIR in that jurisdiction:
a suspect via LINKED_TO_FIR, a phone via a suspect who uses it, a FIR by its own jurisdiction. Financial accounts have
no direct link to FIRs, so they are anchored through suspects linked to the account (fail closed: an unlinked account
is invisible to scoped officers). Unscoped roles (scope=None) skip every check.
"""

from __future__ import annotations

from typing import Optional

# Cypher predicate on a Suspect variable `s`; needs the $scope parameter.
SUSPECT_IN_SCOPE = "EXISTS { (s)-[:LINKED_TO_FIR]->(:FIR {jurisdiction: $scope}) }"

_NODE_IN_SCOPE = f"""
MATCH (n {{id: $id}})
WHERE (n:FIR AND n.jurisdiction = $scope)
   OR (n:Suspect AND EXISTS {{ (n)-[:LINKED_TO_FIR]->(:FIR {{jurisdiction: $scope}}) }})
   OR (n:Phone AND EXISTS {{ (s:Suspect)-[:USES_PHONE]->(n) WHERE {SUSPECT_IN_SCOPE} }})
   OR (n:FinancialAccount AND EXISTS {{ (s:Suspect) WHERE (s.id CONTAINS n.id OR EXISTS {{ (s)-[:LINKED_TO_FIR]->(f:FIR) WHERE f.id CONTAINS n.id }}) AND {SUSPECT_IN_SCOPE} }})
RETURN count(n) AS n
"""

_OUT_OF_SCOPE_ON_PATH = """
UNWIND $nodes AS item
OPTIONAL MATCH (s:Suspect {id: item.id}) WHERE item.label = 'Suspect' AND NOT EXISTS { (s)-[:LINKED_TO_FIR]->(:FIR {jurisdiction: $scope}) }
OPTIONAL MATCH (f:FIR {id: item.id}) WHERE item.label = 'FIR' AND f.jurisdiction <> $scope
RETURN count(s) + count(f) AS outside
"""


def node_in_scope(session, node_id: str, scope: Optional[str]) -> bool:
    if scope is None:
        return True
    row = session.run(_NODE_IN_SCOPE, id=node_id, scope=scope).single()
    return bool(row and row["n"])


def path_in_scope(session, nodes: list[dict], scope: Optional[str]) -> bool:
    """True when no suspect or FIR on the path lies outside `scope`. nodes: [{label, id}]."""
    if scope is None:
        return True
    row = session.run(_OUT_OF_SCOPE_ON_PATH, nodes=nodes, scope=scope).single()
    return bool(row) and row["outside"] == 0
